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


class TestRunHttpActionHeaders:
    def test_normal_headers_passed_and_trust_env_false(self, monkeypatch):
        from opal_event.actions.http import run_http_action
        from opal_event.models import HttpAction

        action = HttpAction(
            id="test_http",
            type="http",
            method="POST",
            url="http://127.0.0.1:9000/notify",
            headers={
                "X-Static": "constant-value",
                "X-Dynamic": "prefix-{{data.name}}",
            },
            body_mode="none",
        )
        event = {"topic": "test", "data": {"name": "alice"}}
        captured: dict = {}

        import httpx

        original_client_init = httpx.Client.__init__

        def mock_client_init(self, *args, **kwargs):
            captured["trust_env"] = kwargs.get("trust_env")
            return original_client_init(self, *args, **kwargs)

        def mock_send(self, request, stream=False):
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(httpx.Client, "__init__", mock_client_init)
        monkeypatch.setattr(httpx.Client, "send", mock_send)

        outcome = run_http_action(action, event, timeout=5, max_output_bytes=1000, allowlist=ALLOWLIST)
        assert outcome.status == "succeeded"
        assert outcome.http_status == 200
        assert captured["trust_env"] is False
        assert captured["headers"]["x-static"] == "constant-value"
        assert captured["headers"]["x-dynamic"] == "prefix-alice"

    def test_invalid_header_value_non_string_fails(self):
        from opal_event.actions.http import run_http_action
        from opal_event.models import HttpAction

        action = HttpAction(
            id="test_http",
            type="http",
            method="POST",
            url="http://127.0.0.1:9000/notify",
            headers={
                "X-Count": "{{data.count}}",
            },
            body_mode="none",
        )
        event = {"topic": "test", "data": {"count": 123}}
        outcome = run_http_action(action, event, timeout=5, max_output_bytes=1000, allowlist=ALLOWLIST)
        assert outcome.status == "failed"
        assert outcome.error_code == "invalid_header_value"
        assert "must be a string" in outcome.error_message


class TestRunHttpActionStreaming:
    def test_small_response_preserves_json_result(self, monkeypatch):
        from opal_event.actions.http import run_http_action
        from opal_event.models import HttpAction

        action = HttpAction(
            id="test_http",
            type="http",
            method="POST",
            url="http://127.0.0.1:9000/notify",
            body_mode="none",
        )

        import httpx

        response_closed = False

        class MockResponse:
            status_code = 200

            def iter_bytes(self):
                yield b'{"status": "ok", "value": 42}'

            def close(self):
                nonlocal response_closed
                response_closed = True

        def mock_send(self, request, stream=False):
            assert stream is True
            return MockResponse()

        monkeypatch.setattr(httpx.Client, "send", mock_send)

        outcome = run_http_action(action, {}, timeout=5, max_output_bytes=1000, allowlist=ALLOWLIST)
        assert outcome.status == "succeeded"
        assert outcome.http_status == 200
        assert outcome.result == {"status": "ok", "value": 42}
        assert response_closed is True

    def test_oversized_response_streaming_truncation_and_close(self, monkeypatch):
        from opal_event.actions.http import run_http_action
        from opal_event.models import HttpAction

        action = HttpAction(
            id="test_http",
            type="http",
            method="POST",
            url="http://127.0.0.1:9000/notify",
            body_mode="none",
        )

        import httpx

        chunks_yielded = 0
        response_closed = False

        class MockOversizedResponse:
            status_code = 200

            def iter_bytes(self):
                nonlocal chunks_yielded
                for _ in range(100):
                    chunks_yielded += 1
                    yield b"0123456789" * 100  # 1000 bytes per chunk

            def close(self):
                nonlocal response_closed
                response_closed = True

        def mock_send(self, request, stream=False):
            assert stream is True
            return MockOversizedResponse()

        monkeypatch.setattr(httpx.Client, "send", mock_send)

        # max_output_bytes is 50. It should read up to 50 bytes and then stop streaming & close.
        outcome = run_http_action(action, {}, timeout=5, max_output_bytes=50, allowlist=ALLOWLIST)
        assert outcome.status == "succeeded"
        assert outcome.http_status == 200
        assert isinstance(outcome.result, str)
        assert outcome.result.endswith("...[truncated]")
        assert outcome.result.startswith("0123456789")
        # Output before "...[truncated]" should be exactly 50 bytes
        prefix = outcome.result[: -len("...[truncated]")]
        assert len(prefix.encode("utf-8")) == 50
        assert response_closed is True
        # Ensure we stopped streaming chunks early instead of reading all 100 chunks
        assert chunks_yielded == 1


