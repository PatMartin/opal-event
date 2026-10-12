#!/usr/bin/env bash
# Sample shell action script (interpreter mode "bash"). Registered action.script_path must point
# to a statically allowlisted file like this one; event data reaches the script only as argv
# entries or JSON on stdin, never interpolated into executable source.
set -euo pipefail

arg1="${1:-unknown}"
echo "ARG1: ${arg1}"
