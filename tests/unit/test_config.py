import pytest
import yaml

from opal_event.config import ConcurrentUpdateError, ConfigStore
from tests.conftest import base_config, free_port


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "opal_event.yaml"
    path.write_text(yaml.safe_dump(base_config(tmp_path), sort_keys=False), encoding="utf-8")
    return path


class TestConfigStore:
    def test_loads_initial_settings(self, config_file):
        store = ConfigStore(config_file)
        assert store.settings.version == 1
        assert store.revision

    def test_update_persists_atomically(self, config_file):
        store = ConfigStore(config_file)
        new_settings = store.update(lambda raw: {**raw, "execution": {**raw["execution"], "max_workers": 7}})
        assert new_settings.execution.max_workers == 7
        # Re-reading from disk should reflect the write.
        store2 = ConfigStore(config_file)
        assert store2.settings.execution.max_workers == 7

    def test_update_rejects_stale_revision(self, config_file):
        store = ConfigStore(config_file)
        stale_revision = store.revision
        store.update(lambda raw: {**raw, "execution": {**raw["execution"], "max_workers": 9}})
        with pytest.raises(ConcurrentUpdateError):
            store.update(lambda raw: raw, expected_revision=stale_revision)

    def test_invalid_mutation_does_not_corrupt_state(self, config_file):
        store = ConfigStore(config_file)
        original_revision = store.revision

        def bad_mutate(raw):
            raw = dict(raw)
            raw["execution"] = {**raw["execution"], "max_workers": -1}  # invalid: must be >= 1
            return raw

        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            store.update(bad_mutate)
        assert store.revision == original_revision

    def test_reload_from_disk_detects_pending_restart(self, config_file):
        store = ConfigStore(config_file)
        raw = yaml.safe_load(config_file.read_text())
        raw["server"]["port"] = free_port()
        config_file.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        changed = store.reload_from_disk()
        assert changed
        assert store.pending_restart

    def test_reload_from_disk_ignores_invalid_yaml(self, config_file):
        store = ConfigStore(config_file)
        good_revision = store.revision
        config_file.write_text("not: [valid, yaml, {{{", encoding="utf-8")
        changed = store.reload_from_disk()
        assert not changed
        assert store.revision == good_revision

    def test_on_reload_callback_invoked(self, config_file):
        calls = []
        store = ConfigStore(config_file, on_reload=lambda old, new: calls.append((old, new)))
        store.update(lambda raw: {**raw, "execution": {**raw["execution"], "max_workers": 3}})
        assert len(calls) == 1
        assert calls[0][1].execution.max_workers == 3


class TestPendingRestart:
    @staticmethod
    def _rewrite(path, mutate):
        raw = yaml.safe_load(path.read_text())
        mutate(raw)
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    def test_no_pending_restart_initially(self, config_file):
        assert ConfigStore(config_file).pending_restart is False

    def test_restart_only_change_sets_flag(self, config_file):
        store = ConfigStore(config_file)
        self._rewrite(config_file, lambda r: r["server"].__setitem__("port", free_port()))
        assert store.reload_from_disk()
        assert store.pending_restart is True

    def test_reverting_change_clears_flag(self, config_file):
        store = ConfigStore(config_file)
        original = config_file.read_text()
        self._rewrite(config_file, lambda r: r["server"].__setitem__("port", free_port()))
        assert store.reload_from_disk()
        assert store.pending_restart is True
        config_file.write_text(original, encoding="utf-8")
        assert store.reload_from_disk()
        assert store.pending_restart is False

    def test_update_revert_clears_flag(self, config_file):
        store = ConfigStore(config_file)
        original_port = store.settings.server.port
        store.update(lambda r: {**r, "server": {**r["server"], "port": free_port()}})
        assert store.pending_restart is True
        store.update(lambda r: {**r, "server": {**r["server"], "port": original_port}})
        assert store.pending_restart is False

    def test_multiple_restart_only_changes(self, config_file):
        store = ConfigStore(config_file)

        def mutate(r):
            r["server"]["port"] = free_port()
            r["zeromq"]["publishers"] = f"tcp://127.0.0.1:{free_port()}"

        self._rewrite(config_file, mutate)
        assert store.reload_from_disk()
        assert store.pending_restart is True

    def test_invalid_reload_preserves_pending_state(self, config_file):
        store = ConfigStore(config_file)
        config_file.write_text("bogus: [unclosed\n", encoding="utf-8")
        assert store.reload_from_disk() is False
        assert store.pending_restart is False
        assert store.reload_failed is True

    def test_successful_reload_after_invalid(self, config_file):
        store = ConfigStore(config_file)
        valid = config_file.read_text()
        config_file.write_text("bogus: [unclosed\n", encoding="utf-8")
        assert store.reload_from_disk() is False
        config_file.write_text(valid, encoding="utf-8")
        self._rewrite(config_file, lambda r: r["server"].__setitem__("port", free_port()))
        assert store.reload_from_disk()
        assert store.reload_failed is False
        assert store.pending_restart is True

    def test_live_setting_update_does_not_set_flag(self, config_file):
        store = ConfigStore(config_file)
        store.update(lambda r: {**r, "execution": {**r["execution"], "queue_size": 42}})
        assert store.pending_restart is False

    def test_restart_only_change_via_update_does_not_activate(self, config_file):
        """Desired value changes but the running binding is untouched — flag marks it pending."""
        store = ConfigStore(config_file)
        new_port = free_port()
        store.update(lambda r: {**r, "server": {**r["server"], "port": new_port}})
        assert store.pending_restart is True
        assert store.settings.server.port == new_port  # desired value persisted, flag tells ops to restart
