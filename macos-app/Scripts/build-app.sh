#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
app_root="${script_dir:h}"
cd "$app_root"

bin_dir="$(swift build -c release --show-bin-path)"
swift build -c release --product JLAgentApp

app_dir="$bin_dir/JL Agent.app"
contents="$app_dir/Contents"
mkdir -p "$contents/MacOS" "$contents/Resources"
cp "$bin_dir/JLAgentApp" "$contents/MacOS/JLAgentApp"
cp "$app_root/Resources/Info.plist" "$contents/Info.plist"
chmod 0755 "$contents/MacOS/JLAgentApp"
codesign --force --sign - "$app_dir"
codesign --verify --strict "$app_dir"

print -r -- "$app_dir"
