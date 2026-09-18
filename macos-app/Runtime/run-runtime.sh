#!/bin/zsh
set -euo pipefail

runtime_root="${0:A:h}"
lazy_target="${HOME}/Library/Caches/JL Agent/python"
packaged_python="$runtime_root/python"
export PYTHONPATH="$runtime_root/src:$runtime_root/upstream/hermes-agent:$packaged_python:$lazy_target"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

python_candidates=(
  "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
  "/opt/homebrew/bin/python3.13"
  "/usr/local/bin/python3.13"
  "/usr/bin/python3"
)

for python_path in $python_candidates; do
  if [[ -x "$python_path" ]]; then
    exec "$python_path" -B -m jl_agent.runtime_service "$@"
  fi
done

print -u2 -- "JL Agent requires a supported Python 3.13 runtime."
exit 69
