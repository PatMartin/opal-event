#!/usr/bin/env bash
# ==============================================================================
# OPAL Event CLI Test Suite (scripts/test-events.sh)
# ==============================================================================
#
# DESCRIPTION:
#   Comprehensive automated integration and end-to-end regression test suite
#   for the OPAL Event command-line interface (`opal-event`).
#
# CLI INVOCATION PATTERN:
#   opal-event <arguments>
#
# CAPABILITIES TESTED:
#   1. CLI Discovery & Help Commands
#      - opal-event --help
#      - Subcommand help: rules, actions, executions, settings, backup
#   2. Service Health, Readiness & Diagnostics
#      - opal-event health
#      - opal-event health --live (unauthenticated liveness probe)
#      - opal-event health --ready (unauthenticated readiness probe)
#      - opal-event stats & opal-event --json stats
#   3. Portable Service Settings
#      - opal-event settings get & opal-event --json settings get
#      - opal-event settings update --json '<patch>'
#   4. Action Management Lifecycle
#      - opal-event actions list
#      - opal-event actions create --json '<action>'
#      - opal-event actions create --file '<path>'
#      - opal-event actions get <action_id>
#      - opal-event actions execute <action_id> --event-json '<event>'
#      - opal-event actions update <action_id> --json '<patch>'
#      - opal-event actions delete <action_id>
#   5. Rule Management Lifecycle
#      - opal-event rules list
#      - opal-event rules create --json '<rule>'
#      - opal-event rules create --file '<path>'
#      - opal-event rules get <rule_id>
#      - opal-event rules test <rule_id> --event-json '<event>' (matching & non-matching)
#      - opal-event rules update <rule_id> --json '<patch>'
#      - opal-event rules delete <rule_id>
#   6. Precondition & Dependency Conflict Enforcement
#      - opal-event actions delete <action_id> (fails with HTTP 409 when rule references action)
#   7. Event Bus Publishing & Inspection
#      - opal-event publish <topic> --data-json '<data>'
#      - opal-event publish with custom --id, --source, and --timestamp
#      - opal-event list
#      - opal-event list with filters: --topic, --source, --limit
#      - opal-event get <event_id>
#      - opal-event topics
#      - opal-event delete <event_id>
#      - opal-event clear
#   8. Action Execution History
#      - opal-event executions list
#      - opal-event executions list with filters: --status, --action-id
#      - opal-event executions get <execution_id>
#   9. Configuration Backup Lifecycle
#      - opal-event backup export & opal-event backup export --output '<file>'
#      - opal-event backup validate --file '<file>'
#      - opal-event backup validate --json '<valid-json>'
#      - opal-event backup validate --json '<invalid-json>' (verifies exit code 1)
#      - opal-event backup import --file '<file>'
#   10. Global Options & Correlation Context Headers
#      - opal-event --json <command>
#      - opal-event --timeout <seconds> <command>
#      - opal-event --opal-session-id <id> --opal-transaction-id <id> --opal-causation-id <id> <command>
#   11. Error Handling & Exit Codes
#      - HTTP 404 resource not found -> exit code 1
#      - HTTP 422 payload validation error -> exit code 1
#      - CLI option/syntax errors -> exit code 2
#      - Unreachable service -> exit code 2
#
# RUNNING THE TEST SUITE:
#   bash scripts/test-events.sh
#   ./scripts/test-events.sh
#
# PREREQUISITES:
#   The Python virtual environment must contain `opal-event` and `opal-event-svc`.
#   This script activates .venv if not already active. If no running server is
#   detected, an isolated temporary test service instance is automatically
#   started on ephemeral ports with a clean temporary SQLite database, and
#   shut down cleanly on script completion.
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# 1. Virtual Environment Activation
# ------------------------------------------------------------------------------
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${VIRTUAL_ENV:-}" ] || ! command -v opal-event >/dev/null 2>&1; then
    if [ -f "${REPO_ROOT}/.venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "${REPO_ROOT}/.venv/bin/activate"
    elif [ -f "/gh/opal/opal-event/.venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "/gh/opal/opal-event/.venv/bin/activate"
    else
        echo "Error: Python virtual environment not found (.venv/bin/activate)." >&2
        exit 1
    fi
fi

if ! command -v opal-event >/dev/null 2>&1; then
    echo "Error: 'opal-event' CLI executable not found in PATH after virtualenv activation." >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# 2. Test Harness & State Tracking
# ------------------------------------------------------------------------------
PASSED=0
FAILED=0
TOTAL=0

assert_success() {
    local test_name="$1"
    shift
    TOTAL=$((TOTAL + 1))
    echo -n "  [TEST $((TOTAL))] ${test_name} ... "
    local output
    if output="$("$@" 2>&1)"; then
        echo "PASS"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo "FAIL (unexpected exit $?)"
        echo "         Command: $*"
        echo "         Output:  ${output}"
        FAILED=$((FAILED + 1))
        return 1
    fi
}

assert_exit_code() {
    local expected_code="$1"
    local test_name="$2"
    shift 2
    TOTAL=$((TOTAL + 1))
    echo -n "  [TEST $((TOTAL))] ${test_name} ... "
    local output
    set +e
    output="$("$@" 2>&1)"
    local actual_code=$?
    set -e
    if [ "${actual_code}" -eq "${expected_code}" ]; then
        echo "PASS"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo "FAIL (expected exit ${expected_code}, got ${actual_code})"
        echo "         Command: $*"
        echo "         Output:  ${output}"
        FAILED=$((FAILED + 1))
        return 1
    fi
}

assert_contains() {
    local substring="$1"
    local test_name="$2"
    shift 2
    TOTAL=$((TOTAL + 1))
    echo -n "  [TEST $((TOTAL))] ${test_name} ... "
    local output
    set +e
    output="$("$@" 2>&1)"
    local actual_code=$?
    set -e
    if [ "${actual_code}" -eq 0 ] && [[ "${output}" == *"${substring}"* ]]; then
        echo "PASS"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo "FAIL (output did not contain expected text, or exited ${actual_code})"
        echo "         Command:  $*"
        echo "         Expected: ${substring}"
        echo "         Output:   ${output}"
        FAILED=$((FAILED + 1))
        return 1
    fi
}

assert_json() {
    local test_name="$1"
    shift
    TOTAL=$((TOTAL + 1))
    echo -n "  [TEST $((TOTAL))] ${test_name} ... "
    local output
    set +e
    output="$("$@" 2>&1)"
    local actual_code=$?
    set -e
    if [ "${actual_code}" -eq 0 ] && echo "${output}" | python3 -m json.tool >/dev/null 2>&1; then
        echo "PASS"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo "FAIL (output was not valid JSON or command exited ${actual_code})"
        echo "         Command: $*"
        echo "         Output:  ${output}"
        FAILED=$((FAILED + 1))
        return 1
    fi
}

# ------------------------------------------------------------------------------
# 3. Isolated Test Server Provisioning
# ------------------------------------------------------------------------------
TEST_TMPDIR="$(mktemp -d /tmp/opal_cli_test_XXXXXX)"
SERVER_PID=""

cleanup() {
    local exit_code=$?
    if [ -n "${SERVER_PID}" ]; then
        echo ""
        echo "Shutting down isolated test service (PID: ${SERVER_PID})..."
        kill -TERM "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
    if [ -d "${TEST_TMPDIR}" ]; then
        rm -rf "${TEST_TMPDIR}"
    fi
    exit "${exit_code}"
}
trap cleanup EXIT INT TERM

echo "=============================================================================="
echo "OPAL Event CLI Test Suite"
echo "=============================================================================="

# Generate an isolated YAML configuration bound to ephemeral localhost ports
python3 -c "
import socket, yaml, os

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

tmpdir = '${TEST_TMPDIR}'
p_server = free_port()
p_pub = free_port()
p_sub = free_port()

cfg = {
    'version': 1,
    'server': {'host': '127.0.0.1', 'port': p_server},
    'zeromq': {
        'publishers': f'tcp://127.0.0.1:{p_pub}',
        'subscribers': f'tcp://127.0.0.1:{p_sub}',
        'max_event_bytes': 1048576,
        'publisher_queue_size': 1000
    },
    'security': {
        'rest_auth': {'enabled': False}
    },
    'execution': {
        'max_workers': 2,
        'queue_size': 50,
        'default_timeout_seconds': 5,
        'overflow': 'drop_and_log',
        'retry_enabled': False,
        'max_output_bytes': 65536
    },
    'persistence': {
        'database': os.path.join(tmpdir, 'test.db'),
        'event_mode': 'all',
        'execution_history_enabled': True
    },
    'actions': [
        {
            'id': 'bootstrap_action',
            'type': 'shell',
            'enabled': True,
            'mode': 'exec',
            'executable': '/bin/echo',
            'args': ['bootstrap: {{data.msg}}'],
            'stdin': 'none',
            'timeout_seconds': 5
        }
    ],
    'rules': [
        {
            'id': 'bootstrap_rule',
            'enabled': True,
            'topic': 'bootstrap.*',
            'conditions': [],
            'actions': ['bootstrap_action']
        }
    ]
}

with open(os.path.join(tmpdir, 'config.yaml'), 'w') as f:
    yaml.dump(cfg, f)

with open(os.path.join(tmpdir, 'server_port.txt'), 'w') as f:
    f.write(str(p_server))
"

SERVER_PORT="$(cat "${TEST_TMPDIR}/server_port.txt")"
export OPAL_EVENT_API_URL="http://127.0.0.1:${SERVER_PORT}"

echo "Starting isolated test service on port ${SERVER_PORT}..."
opal-event-svc --config "${TEST_TMPDIR}/config.yaml" --log-level error &
SERVER_PID=$!

READY=0
for _ in {1..40}; do
    if opal-event health --ready >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.1
done

if [ "${READY}" -ne 1 ]; then
    echo "Error: Timed out waiting for test server to respond on ${OPAL_EVENT_API_URL}." >&2
    exit 1
fi
echo "Service ready (PID: ${SERVER_PID}, Base URL: ${OPAL_EVENT_API_URL})"
echo ""

# ------------------------------------------------------------------------------
# 4. CLI Test Suite Execution
# ------------------------------------------------------------------------------

echo "--- 1. CLI Discovery & Help Commands ---"
assert_contains "event bus, rules, and action execution" "Top-level help" opal-event --help
assert_contains "Manage rules that match events to actions" "Rules subcommand help" opal-event rules --help
assert_contains "Manage registered actions" "Actions subcommand help" opal-event actions --help
assert_contains "Inspect action execution history" "Executions subcommand help" opal-event executions --help
assert_contains "Portable, live-adjustable service settings" "Settings subcommand help" opal-event settings --help
assert_contains "Export, validate, and import portable configuration" "Backup subcommand help" opal-event backup --help

echo ""
echo "--- 2. Service Health, Readiness & Diagnostics ---"
assert_contains "status" "Health summary check" opal-event health
assert_contains "ok" "Liveness probe (health --live)" opal-event health --live
assert_contains "ok" "Readiness probe (health --ready)" opal-event health --ready
assert_contains "observed_events" "Stats summary" opal-event stats
assert_json "Stats formatted as JSON" opal-event --json stats

echo ""
echo "--- 3. Portable Settings Management ---"
assert_contains "max_event_bytes" "Get portable settings" opal-event settings get
assert_json "Get portable settings as JSON" opal-event --json settings get
assert_success "Update portable settings (max_event_bytes)" opal-event settings update --json '{"max_event_bytes": 524288}'
assert_contains "524288" "Verify updated settings" opal-event --json settings get

echo ""
echo "--- 4. Action Management Lifecycle ---"
assert_contains "bootstrap_action" "List registered actions" opal-event actions list
assert_json "List actions as JSON" opal-event --json actions list

ACTION_PAYLOAD='{
  "id": "cli_shell_action",
  "type": "shell",
  "enabled": true,
  "mode": "exec",
  "executable": "/bin/echo",
  "args": ["{{data.msg}}"],
  "stdin": "none",
  "timeout_seconds": 5
}'
assert_success "Create action with --json" opal-event actions create --json "${ACTION_PAYLOAD}"
assert_contains "cli_shell_action" "Get action by ID" opal-event actions get cli_shell_action

# Create action using --file
FILE_ACTION_PATH="${TEST_TMPDIR}/file_action.json"
cat <<'EOF' > "${FILE_ACTION_PATH}"
{
  "id": "cli_file_action",
  "type": "shell",
  "enabled": true,
  "mode": "exec",
  "executable": "/bin/echo",
  "args": ["hello file"],
  "stdin": "none",
  "timeout_seconds": 5
}
EOF
assert_success "Create action with --file" opal-event actions create --file "${FILE_ACTION_PATH}"
assert_contains "cli_file_action" "Get file-created action" opal-event actions get cli_file_action

assert_success "Update action (disable)" opal-event actions update cli_shell_action --json '{"enabled": false}'
assert_contains "no" "Verify action disabled" opal-event actions get cli_shell_action
assert_success "Update action (re-enable)" opal-event actions update cli_shell_action --json '{"enabled": true}'

assert_success "Execute action manually" opal-event actions execute cli_shell_action --event-json '{"topic": "cli.manual", "data": {"msg": "exec_test"}}'
assert_success "Delete standalone action" opal-event actions delete cli_file_action

echo ""
echo "--- 5. Rule Management Lifecycle ---"
assert_contains "bootstrap_rule" "List rules" opal-event rules list
assert_json "List rules as JSON" opal-event --json rules list

RULE_PAYLOAD='{
  "id": "cli_test_rule",
  "enabled": true,
  "topic": "cli.test.*",
  "conditions": [
    {
      "path": "data.trigger",
      "operator": "eq",
      "value": "now"
    }
  ],
  "actions": [
    "cli_shell_action"
  ]
}'
assert_success "Create rule with --json" opal-event rules create --json "${RULE_PAYLOAD}"
assert_contains "cli_test_rule" "Get rule by ID" opal-event rules get cli_test_rule

# Create rule using --file
FILE_RULE_PATH="${TEST_TMPDIR}/file_rule.json"
cat <<'EOF' > "${FILE_RULE_PATH}"
{
  "id": "cli_file_rule",
  "enabled": true,
  "topic": "cli.temp.*",
  "conditions": [],
  "actions": ["cli_shell_action"]
}
EOF
assert_success "Create rule with --file" opal-event rules create --file "${FILE_RULE_PATH}"
assert_contains "cli_file_rule" "Get file-created rule" opal-event rules get cli_file_rule

assert_contains '"matched": true' "Test rule with matching event" opal-event rules test cli_test_rule --event-json '{"topic": "cli.test.run", "data": {"trigger": "now"}}'
assert_contains '"matched": false' "Test rule with non-matching event" opal-event rules test cli_test_rule --event-json '{"topic": "cli.test.run", "data": {"trigger": "later"}}'

assert_success "Update rule (disable)" opal-event rules update cli_test_rule --json '{"enabled": false}'
assert_contains "no" "Verify rule disabled" opal-event rules get cli_test_rule
assert_success "Update rule (re-enable)" opal-event rules update cli_test_rule --json '{"enabled": true}'
assert_success "Delete standalone rule" opal-event rules delete cli_file_rule

echo ""
echo "--- 6. Action-Rule Dependency Enforcement ---"
# Deleting an action currently referenced by an active rule must fail with HTTP 409 conflict (exit code 1)
assert_exit_code 1 "Reject action deletion when referenced by rule (409 Conflict)" opal-event actions delete cli_shell_action

echo ""
echo "--- 7. Event Bus Publishing & Inspection ---"
assert_success "Publish event" opal-event publish cli.test.event --data-json '{"trigger": "now", "msg": "hello from publish"}'
assert_success "Publish event with custom ID, source, timestamp" opal-event publish cli.custom.event --data-json '{"foo": "bar"}' --id "custom-evt-001" --source "cli-suite" --timestamp "2026-10-24T20:00:00Z"

# Wait briefly for persistence
sleep 0.4

assert_contains "custom-evt-001" "List events" opal-event list
assert_json "List events as JSON" opal-event --json list
assert_contains "custom-evt-001" "List events filtered by topic" opal-event list --topic cli.custom.event
assert_contains "custom-evt-001" "List events filtered by source" opal-event list --source cli-suite
assert_success "List events with limit" opal-event list --limit 1
assert_contains "custom-evt-001" "Get event by ID" opal-event get custom-evt-001
assert_contains "cli." "List observed topics" opal-event topics
assert_success "Delete event by ID" opal-event delete custom-evt-001
assert_exit_code 1 "Get deleted event returns 404" opal-event get custom-evt-001
assert_success "Clear all events" opal-event clear

echo ""
echo "--- 8. Action Execution History ---"
assert_success "List action executions" opal-event executions list
assert_json "List executions as JSON" opal-event --json executions list
assert_success "List executions filtered by status" opal-event executions list --status succeeded
assert_success "List executions filtered by action ID" opal-event executions list --action-id cli_shell_action

# Fetch execution ID from JSON list
EXECUTION_ID="$(opal-event --json executions list | python3 -c "import sys, json; data = json.load(sys.stdin); print(data[0]['id'] if data else '')")"
if [ -n "${EXECUTION_ID}" ]; then
    assert_contains "${EXECUTION_ID}" "Get execution details by ID" opal-event executions get "${EXECUTION_ID}"
else
    echo "  [SKIP] Executions list empty; skipping executions get"
fi

echo ""
echo "--- 9. Configuration Backup Lifecycle ---"
BACKUP_FILE="${TEST_TMPDIR}/backup_export.json"
assert_success "Export configuration to file" opal-event backup export --output "${BACKUP_FILE}"
assert_json "Export configuration to stdout" opal-event backup export

assert_contains '"valid": true' "Validate exported backup file" opal-event backup validate --file "${BACKUP_FILE}"
assert_contains '"valid": true' "Validate backup via --json" opal-event backup validate --json '{"format_version": 1, "settings": {"max_event_bytes": 1048576, "execution": {}, "persistence": {}}, "actions": [], "rules": []}'
assert_exit_code 1 "Reject invalid backup via --json (validation error)" opal-event backup validate --json '{"invalid": true}'

# Wrap backup into BackupImportRequest
IMPORT_FILE="${TEST_TMPDIR}/backup_import.json"
python3 -c "
import json
with open('${BACKUP_FILE}') as f:
    b = json.load(f)
with open('${IMPORT_FILE}', 'w') as f:
    json.dump({'backup': b}, f)
"
assert_success "Import configuration from file" opal-event backup import --file "${IMPORT_FILE}"

echo ""
echo "--- 10. Rule and Action Cleanup ---"
assert_success "Delete rule" opal-event rules delete cli_test_rule
assert_success "Delete action after rule removal" opal-event actions delete cli_shell_action

echo ""
echo "--- 11. Global Options & Correlation Context Headers ---"
assert_json "Global --json flag output" opal-event --json health
assert_success "Global --timeout option" opal-event --timeout 15.0 health
assert_success "Global correlation headers (session, transaction, causation)" opal-event --opal-session-id "sess-123" --opal-transaction-id "tx-456" --opal-causation-id "cause-789" health

echo ""
echo "--- 12. Error Handling & Exit Codes ---"
assert_exit_code 1 "HTTP 404 for nonexistent event" opal-event get nonexistent-event-id-999
assert_exit_code 1 "HTTP 404 for nonexistent rule" opal-event rules get nonexistent-rule-id-999
assert_exit_code 1 "HTTP 404 for nonexistent action" opal-event actions get nonexistent-action-id-999
assert_exit_code 1 "HTTP 422 for malformed rule payload" opal-event rules create --json '{"id": "bad", "actions": "not-a-list"}'
assert_exit_code 2 "Exit 2 for invalid CLI option" opal-event --invalid-flag-xyz
assert_exit_code 2 "Exit 2 for unknown subcommand" opal-event nonexistent-subcommand-xyz
assert_exit_code 2 "Exit 2 when API service is unreachable" opal-event --api-url "http://127.0.0.1:1" --timeout 0.1 health

# ------------------------------------------------------------------------------
# 5. Summary and Results
# ------------------------------------------------------------------------------
echo ""
echo "=============================================================================="
echo "TEST SUMMARY: ${PASSED}/${TOTAL} passed, ${FAILED} failed"
echo "=============================================================================="

if [ "${FAILED}" -eq 0 ]; then
    echo "All OPAL Event CLI capability tests passed successfully."
    exit 0
else
    echo "Some tests failed. Review the output above."
    exit 1
fi
