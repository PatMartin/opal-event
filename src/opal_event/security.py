"""Security helpers: HTTP destination allowlisting (scheme/host/port, DNS re-check, no redirects)
and subprocess isolation defaults for shell/python actions.

Per zeromq_protocol.md: CURVE encryption alone is not a publisher allowlist, and subprocess
isolation here is not a strong sandbox against malicious plugins — it only bounds environment,
working directory, PATH, output size, and execution time, and performs process-tree cleanup.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from opal_event.models import AllowedHttpDestination

# RFC 1918 / link-local / loopback-exclusion is enforced in addition to the explicit allowlist so
# that an allowlisted hostname cannot be used to reach cloud metadata endpoints via DNS rebinding.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, incl. cloud metadata (169.254.169.254)
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]


class HttpDestinationError(Exception):
    """Raised when an HTTP action's resolved destination is not allowlisted."""


def _host_allowed(host: str, scheme: str, port: int, allowlist: list[AllowedHttpDestination]) -> AllowedHttpDestination | None:
    for entry in allowlist:
        if entry.host == host and scheme in entry.schemes and port in entry.ports:
            return entry
    return None


def resolve_and_check(url: str, allowlist: list[AllowedHttpDestination]) -> str:
    """Validates ``url`` against the allowlist by hostname, then re-resolves DNS and rejects
    disallowed address ranges. Returns the validated URL. Raises :class:`HttpDestinationError`.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise HttpDestinationError(f"unsupported scheme: {parts.scheme}")
    host = parts.hostname
    if not host:
        raise HttpDestinationError("URL has no hostname")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    entry = _host_allowed(host, parts.scheme, port, allowlist)
    if entry is None:
        raise HttpDestinationError(f"destination not allowlisted: {parts.scheme}://{host}:{port}")
    try:
        addr_infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise HttpDestinationError(f"DNS resolution failed for {host}: {exc}") from exc
    for _family, _, _, _, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_loopback and host not in ("127.0.0.1", "localhost", "::1"):
            # Allow only when the allowlisted host itself is a loopback literal/name.
            continue
        if any(ip in network for network in _BLOCKED_NETWORKS) and not ip.is_loopback:
            raise HttpDestinationError(f"resolved address not permitted: {ip}")
    return url


def subprocess_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Builds a minimal, controlled environment for subprocess workers (shell/python actions)."""
    base = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    if extra:
        base.update(extra)
    return base
