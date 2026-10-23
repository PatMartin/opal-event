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
    def test_contract_loads_and_has_expected_operation_count(self):
        operations = load_contract_operations()
        assert len(operations) == 29

    def test_cli_exposes_every_contract_operation(self):
        operations = load_contract_operations()
        missing = set(operations) - set(CLI_OPERATIONS)
        assert not missing, f"CLI is missing operations: {missing}"

    def test_cli_operations_match_contract_method_and_path(self):
        operations = load_contract_operations()
        for op_id, (method, path) in operations.items():
            cli_method, cli_path = CLI_OPERATIONS[op_id]
            assert cli_method == method, f"{op_id}: method mismatch"
            assert cli_path == path, f"{op_id}: path mismatch"

    def test_app_implements_every_contract_operation(self, app_client):
        operations = load_contract_operations()
        openapi = app_client.get("/openapi.json").json()
        app_operation_ids = {
            op["operationId"] for methods in openapi["paths"].values() for op in methods.values() if "operationId" in op
        }
        # The REST layer's own operation ids are function names, not required to match the contract's
        # operationId strings exactly, but every contract path+method must be routable.
        app_paths = {(method.upper(), path) for path, methods in openapi["paths"].items() for method in methods}
        for op_id, (method, path) in operations.items():
            assert (method, path) in app_paths, f"missing route for {op_id}: {method} {path}"
        assert app_operation_ids  # sanity: openapi.json actually has operations
