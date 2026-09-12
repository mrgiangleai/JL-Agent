#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
HERMES_DIR="$ROOT_DIR/upstream/hermes-agent"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_TARGET="$HERMES_DIR"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$ROOT_DIR/.jl-agent/pip-cache}"

if [[ "${1:-}" == "--dev" ]]; then
  # Match Hermes' current Python CI lane. The suite disables lazy installs, so
  # provider SDKs exercised by tests must already be present.
  INSTALL_TARGET="$HERMES_DIR[all,dev,anthropic,mistral,fal,modal,daytona,hindsight,parallel-web]"
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--dev]" >&2
  exit 2
fi

if [[ ! -f "$HERMES_DIR/pyproject.toml" ]]; then
  echo "Hermes submodule is missing. Run: git submodule update --init --recursive" >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys

if not ((3, 11) <= sys.version_info[:2] < (3, 14)):
    raise SystemExit(
        f"Hermes requires Python 3.11-3.13; found {sys.version.split()[0]}"
    )
PY

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

mkdir -p "$PIP_CACHE_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$INSTALL_TARGET"

echo "Hermes baseline installed in $VENV_DIR"
echo "Next: $ROOT_DIR/scripts/verify-baseline.sh"
