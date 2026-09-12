#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
HERMES_DIR="$ROOT_DIR/upstream/hermes-agent"
PYTHON="$ROOT_DIR/.venv/bin/python"
HERMES="$ROOT_DIR/.venv/bin/hermes"
TEST_HOME="${HERMES_HOME:-$ROOT_DIR/.jl-agent/verify-home}"
TEST_USER_HOME="${JL_TEST_USER_HOME:-$ROOT_DIR/.jl-agent/verify-user-home}"
EXPECTED_REVISION="044a77b3b6af4ce16138d42762f812a20b9f7a89"

test -f "$ROOT_DIR/.gitmodules"
test -f "$HERMES_DIR/LICENSE"
test -x "$PYTHON"
test -x "$HERMES"
mkdir -p "$TEST_HOME" "$TEST_USER_HOME"
export HERMES_HOME="$TEST_HOME"
export HOME="$TEST_USER_HOME"

actual_revision="$(git -C "$HERMES_DIR" rev-parse HEAD)"
if [[ "$actual_revision" != "$EXPECTED_REVISION" ]]; then
  echo "Unexpected Hermes revision: $actual_revision" >&2
  echo "Expected: $EXPECTED_REVISION" >&2
  exit 1
fi

"$PYTHON" - <<'PY'
import importlib.metadata
import model_tools
import run_agent
import tools.mcp_tool

assert run_agent.AIAgent
assert model_tools.get_tool_definitions
assert tools.mcp_tool
print("hermes-agent", importlib.metadata.version("hermes-agent"))
print("imports: ok")
PY

"$HERMES" --help >/dev/null
echo "cli-help: ok"
echo "baseline: ok"
