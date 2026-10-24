"""Contract tests: the OpenAPI document is authoritative. Every path/method/operationId must be
implemented by the REST app and exposed by the CLI client, with no drift in either direction."""

from __future__ import annotations

from pathlib import Path

import yaml

from opal_event.cli.client import OPERATIONS as CLI_OPERATIONS

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "opal_event.1.2.0.yaml"


def load_contract_operations() -> dict[str, tuple[str, str]]:
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    operations: dict[str, tuple[str, str]] = {}
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            operations[operation["operationId"]] = (method.upper(), path)
    return operations


class TestContractCoverage:
    def test_contract_loads_operations_without_hardcoded_count(self):
        operations = load_contract_operations()
        assert operations, "Contract must define operations"

    def test_stats_contract_includes_invalid_event_counter(self):
        spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        stats_schema = spec["components"]["schemas"]["Stats"]
        assert stats_schema["properties"]["invalid_events"]["type"] == "integer"
        assert "invalid_events" in stats_schema["required"]

    def test_stats_contract_includes_rejected_publications_counter(self):
        spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        stats_schema = spec["components"]["schemas"]["Stats"]
        assert stats_schema["properties"]["rejected_publications"]["type"] == "integer"
        assert "rejected_publications" in stats_schema["required"]

    def test_zeromq_settings_contract_includes_publisher_queue_size(self):
        spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        zmq_schema = spec["components"]["schemas"]["ZeroMqSettings"]
        assert zmq_schema["properties"]["publisher_queue_size"]["type"] == "integer"

    def test_cli_and_contract_operations_match_in_both_directions(self):
        operations = load_contract_operations()
        missing_in_cli = set(operations) - set(CLI_OPERATIONS)
        assert not missing_in_cli, f"CLI is missing operations: {missing_in_cli}"

        missing_in_contract = set(CLI_OPERATIONS) - set(operations)
        assert not missing_in_contract, f"Contract is missing CLI operations: {missing_in_contract}"

    def test_cli_operations_match_contract_method_and_path(self):
        operations = load_contract_operations()
        for op_id, (method, path) in operations.items():
            cli_method, cli_path = CLI_OPERATIONS[op_id]
            assert cli_method == method, f"{op_id}: method mismatch"
            assert cli_path == path, f"{op_id}: path mismatch"

    def test_app_and_contract_operations_match_in_both_directions(self, app_client):
        operations = load_contract_operations()
        contract_paths = set(operations.values())

        openapi = app_client.get("/openapi.json").json()
        app_paths = {
            (method.upper(), path)
            for path, methods in openapi["paths"].items()
            for method in methods
            if method in ("get", "post", "put", "patch", "delete")
        }

        missing_in_app = contract_paths - app_paths
        assert not missing_in_app, f"Contract operations missing in app: {missing_in_app}"

        missing_in_contract = app_paths - contract_paths
        assert not missing_in_contract, f"App public operations missing in contract: {missing_in_contract}"

    def test_event_deletion_operations_documented_in_contract(self):
        spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))

        clear_op = spec["paths"]["/api/v1/events/"]["delete"]
        assert clear_op["operationId"] == "delete_api_v1_events"
        assert "204" in clear_op["responses"]
        assert "401" in clear_op["responses"]
        assert "403" in clear_op["responses"]

        delete_single_op = spec["paths"]["/api/v1/events/{event_id}"]["delete"]
        assert delete_single_op["operationId"] == "delete_api_v1_events_id"
        assert "204" in delete_single_op["responses"]
        assert "401" in delete_single_op["responses"]
        assert "403" in delete_single_op["responses"]
        assert "404" in delete_single_op["responses"]
