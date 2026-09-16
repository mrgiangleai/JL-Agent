#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
app_root="${script_dir:h}"
cd "$app_root"

source "$script_dir/build-config.sh"
jl_configure_swift_build_environment "$app_root"
exec swift run JLAgentNativeTests "$@"
