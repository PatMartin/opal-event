import pytest

from opal_event.models import AllowedHttpDestination
from opal_event.security import HttpDestinationError, resolve_and_check

ALLOWLIST = [AllowedHttpDestination(host="127.0.0.1", ports=[9000], schemes=["http"])]


class TestResolveAndCheck:
    def test_allowed_destination_passes(self):
        assert resolve_and_check("http://127.0.0.1:9000/notify", ALLOWLIST) == "http://127.0.0.1:9000/notify"

    def test_disallowed_host_rejected(self):
        with pytest.raises(HttpDestinationError):
            resolve_and_check("http://example.com:9000/notify", ALLOWLIST)

    def test_disallowed_port_rejected(self):
        with pytest.raises(HttpDestinationError):
            resolve_and_check("http://127.0.0.1:9999/notify", ALLOWLIST)

    def test_disallowed_scheme_rejected(self):
        with pytest.raises(HttpDestinationError):
            resolve_and_check("https://127.0.0.1:9000/notify", ALLOWLIST)

    def test_unsupported_scheme_rejected(self):
        with pytest.raises(HttpDestinationError):
            resolve_and_check("ftp://127.0.0.1:9000/notify", ALLOWLIST)

    def test_link_local_metadata_address_rejected_even_if_allowlisted_by_name(self):
        allowlist = [AllowedHttpDestination(host="169.254.169.254", ports=[80], schemes=["http"])]
        with pytest.raises(HttpDestinationError):
            resolve_and_check("http://169.254.169.254:80/latest/meta-data", allowlist)
