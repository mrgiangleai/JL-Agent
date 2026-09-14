#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"
venv_python="$project_root/.venv/bin/python"

if [[ ! -x "$venv_python" ]]; then
  print -u2 -- "JL development environment is missing: $venv_python"
  print -u2 -- "Create the small project environment before starting the runtime."
  exit 69
fi

export PYTHONPATH="$project_root/src"
exec "$venv_python" -m jl_agent.runtime_service "$@"
