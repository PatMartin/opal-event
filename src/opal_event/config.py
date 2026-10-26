"""YAML-backed configuration loading, atomic persistence, and hot reload.

The on-disk YAML file is the single authoritative source for :class:`~opal_event.models.Settings`
(server/zeromq/security/execution/persistence/actions/rules). Network bindings, REST auth, CURVE
credentials, and the SQLite path require a process restart to take effect; rules/actions/execution/
persistence (portable) fields apply live. See zeromq_protocol.md and opal_event.yaml for the exact
reload semantics this module implements.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import yaml

from opal_event.models import Settings

logger = logging.getLogger("opal_event.config")

# Fields that only take effect after a process restart.
RESTART_ONLY_PATHS: tuple[tuple[str, ...], ...] = (
    ("server",),
    ("zeromq", "publishers"),
    ("zeromq", "subscribers"),
    ("zeromq", "curve"),
    ("security", "rest_auth"),
    ("persistence", "database"),
)


def _revision_of(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]


def _get_path(data: dict, path: tuple[str, ...]):
    current = data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class ConfigError(Exception):
    """Raised when the YAML configuration file is structurally or semantically invalid."""


class ConfigStore:
    """Loads, validates, persists, and hot-reloads the single YAML configuration file.

    Thread-safe: all reads/writes take an internal lock so REST handlers and the filesystem
    watcher never observe a half-written file or race on the revision counter.
    """

    def __init__(self, path: str | Path, on_reload: Callable[[Settings, Settings], None] | None = None):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._on_reload = on_reload
        self._settings: Settings
        self._revision: str
        self._raw_at_load: dict
        self._pending_restart = False
        self._reload_failed = False
        self._load_initial()

    # -- loading -------------------------------------------------------------------

    def _load_initial(self) -> None:
        if not self.path.exists():
            raise ConfigError(f"configuration file not found: {self.path}")
        text = self.path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        settings = Settings.model_validate(raw)
        self._settings = settings
        self._raw_at_load = raw
        self._revision = _revision_of(text)

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    @property
    def revision(self) -> str:
        with self._lock:
            return self._revision

    @property
    def pending_restart(self) -> bool:
        with self._lock:
            return self._pending_restart

    @property
    def reload_failed(self) -> bool:
        """True when the most recent attempted configuration reload (file edit or API write) was
        rejected as invalid and the last-good configuration is still the one in effect."""
        with self._lock:
            return self._reload_failed

    def set_reload_callback(self, callback: Callable[[Settings, Settings], None] | None) -> None:
        """Registers (or replaces) the callback invoked with (old, new) settings after any reload,
        whether triggered by a REST write, a direct YAML edit, or a backup import."""
        with self._lock:
            self._on_reload = callback

    # -- reload (filesystem watcher or explicit poke) ------------------------------

    def reload_from_disk(self) -> bool:
        """Re-reads the YAML file. Invalid content is logged and ignored; last-good stays active.

        Returns True if the active settings changed.
        """
        with self._lock:
            try:
                text = self.path.read_text(encoding="utf-8")
                new_revision = _revision_of(text)
                if new_revision == self._revision:
                    self._reload_failed = False  # file reverted to the active config
                    return False
                raw = yaml.safe_load(text) or {}
                new_settings = Settings.model_validate(raw)
            except Exception:
                logger.exception("rejected invalid configuration reload from %s; keeping last-good config", self.path)
                self._reload_failed = True
                return False
            old_settings = self._settings
            self._detect_pending_restart(self._raw_at_load, raw)
            self._settings = new_settings
            self._raw_at_load = raw
            self._revision = new_revision
            self._reload_failed = False
            if self._on_reload is not None:
                self._on_reload(old_settings, new_settings)
            return True

    def _detect_pending_restart(self, old_raw: dict, new_raw: dict) -> None:
        for path in RESTART_ONLY_PATHS:
            if _get_path(old_raw, path) != _get_path(new_raw, path):
                self._pending_restart = True
                return

    # -- persistence -----------------------------------------------------------------

    def _write_atomic(self, raw: dict) -> str:
        text = yaml.safe_dump(raw, sort_keys=False)
        fd, tmp_path = tempfile.mkstemp(prefix=".opal_event_config_", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
        return _revision_of(text)

    def update(self, mutate: Callable[[dict], dict], expected_revision: str | None = None) -> Settings:
        """Applies ``mutate`` to a copy of the raw YAML dict and persists it atomically.

        Raises :class:`ConcurrentUpdateError` if ``expected_revision`` is given and does not match
        the current on-disk revision (optimistic concurrency per ``If-Match``).
        """
        with self._lock:
            if expected_revision is not None and expected_revision != self._revision:
                raise ConcurrentUpdateError(self._revision)
            new_raw = mutate(dict(self._raw_at_load))
            try:
                new_settings = Settings.model_validate(new_raw)
            except Exception:
                self._reload_failed = True
                raise
            self._detect_pending_restart(self._raw_at_load, new_settings.model_dump(mode="json"))
            new_revision = self._write_atomic(new_settings.model_dump(mode="json"))
            old_settings = self._settings
            self._settings = new_settings
            self._raw_at_load = new_settings.model_dump(mode="json")
            self._revision = new_revision
            self._reload_failed = False
            if self._on_reload is not None:
                self._on_reload(old_settings, new_settings)
            return new_settings


class ConcurrentUpdateError(Exception):
    """Raised when an update's ``If-Match`` revision no longer matches the file on disk."""

    def __init__(self, current_revision: str):
        super().__init__(f"configuration revision mismatch; current revision is {current_revision}")
        self.current_revision = current_revision
