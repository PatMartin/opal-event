#!/usr/bin/env bash
# Isolated end-to-end regression: provisions a throwaway opal-event service on ephemeral
# 127.0.0.1 ports (temp config + temp SQLite DB), runs regression_test.py against it, then
# tears everything down. Used by the e2e-regression CI job; also runnable locally.
# It can never touch a shared/production service: the REST/ZMQ endpoints are generated here.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

if [[ -f ".venv/Scripts/activate" ]]; then
  # shellcheck disable=SC1091
  source .venv/Scripts/activate
elif [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

PYTHON=""
for candidate in python python3; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    PYTHON="${candidate}"
    break
  fi
done
[[ -n "${PYTHON}" ]] || { echo "no python interpreter found" >&2; exit 1; }

native_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

TEST_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/opal_e2e_XXXXXX")"
TMPDIR_NATIVE="$(native_path "${TEST_TMPDIR}")"
SVC_PID=""
cleanup() {
  if [[ -n "${SVC_PID}" ]]; then
    kill "${SVC_PID}" 2>/dev/null || true
    wait "${SVC_PID}" 2>/dev/null || true
  fi
  rm -rf "${TEST_TMPDIR}" || true
}
trap cleanup EXIT

# Generate the isolated config with the test suite's own generator (free ports, temp DB).
"$PYTHON" - "${TMPDIR_NATIVE}" <<'PY'
import pathlib
import sys

import yaml

sys.path.insert(0, "")
from tests.conftest import base_config  # noqa: E402

tmp = pathlib.Path(sys.argv[1])
path = tmp / "opal_event.yaml"
path.write_text(yaml.safe_dump(base_config(tmp), sort_keys=False), encoding="utf-8")
PY

REST_PORT="$("$PYTHON" -c "import yaml,sys; print(yaml.safe_load(open(r'${TMPDIR_NATIVE}/opal_event.yaml'))['server']['port'])")"
ZMQ_PUB="$("$PYTHON" -c "import yaml,sys; print(yaml.safe_load(open(r'${TMPDIR_NATIVE}/opal_event.yaml'))['zeromq']['publishers'])")"
export OPAL_EVENT_API_URL="http://127.0.0.1:${REST_PORT}"
export OPAL_EVENT_ZMQ_PUBLISHERS="${ZMQ_PUB}"
unset OPAL_EVENT_API_TOKEN || true

opal-event-svc --config "${TMPDIR_NATIVE}/opal_event.yaml" --log-level error &
SVC_PID=$!

deadline=$((SECONDS + 30))
until curl -fsS --max-time 2 "${OPAL_EVENT_API_URL}/api/v1/health/ready" >/dev/null 2>&1; do
  if ! kill -0 "${SVC_PID}" 2>/dev/null; then
    echo "opal-event-svc exited during startup" >&2
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    echo "timeout waiting for service readiness" >&2
    exit 1
  fi
  sleep 0.2
done

OUT="${TEST_TMPDIR}/regression.out"
timeout 600 "${PYTHON}" regression_test.py | tee "${OUT}"
if grep -q "SKIP" "${OUT}"; then
  echo "regression suite skipped required checks in CI" >&2
  exit 1
fi
echo "e2e regression suite passed"
