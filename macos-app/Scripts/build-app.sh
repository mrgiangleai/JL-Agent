#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
app_root="${script_dir:h}"
project_root="${app_root:h}"
cd "$app_root"

swift_sdk="${JL_SWIFT_SDKROOT:-/Library/Developer/CommandLineTools/SDKs/MacOSX15.5.sdk}"
if [[ ! -d "$swift_sdk" ]]; then
  print -u2 -- "error: supported Swift SDK is missing: $swift_sdk"
  exit 69
fi
export SDKROOT="$swift_sdk"
export CLANG_MODULE_CACHE_PATH="$app_root/.build/ModuleCache"
export SWIFTPM_MODULECACHE_OVERRIDE="$app_root/.build/ModuleCache"

bin_dir="$(swift build -c release --show-bin-path)"
swift build -c release --product JLAgentApp

app_dir="$bin_dir/JL Agent.app"
signing_identity="${JL_CODE_SIGN_IDENTITY:--}"
contents="$app_dir/Contents"
mkdir -p "$contents/MacOS" "$contents/Resources"
cp "$bin_dir/JLAgentApp" "$contents/MacOS/JLAgentApp"
cp "$app_root/Resources/Info.plist" "$contents/Info.plist"
runtime_bundle="$contents/Resources/JLRuntime"
mkdir -p "$runtime_bundle/src" "$runtime_bundle/config" \
  "$runtime_bundle/upstream/hermes-agent"
rsync -a --delete \
  --exclude='__pycache__/' --exclude='*.py[cod]' \
  "$project_root/src/" "$runtime_bundle/src/"
rsync -a --delete \
  --exclude='local*' --exclude='*.key' --exclude='*.pem' \
  "$project_root/config/" "$runtime_bundle/config/"
rsync -a --delete \
  --exclude='.git/' --exclude='__pycache__/' --exclude='*.py[cod]' \
  --exclude='/tests/' --exclude='/tests-js/' --exclude='/apps/' \
  --exclude='/website/' --exclude='/docs/' --exclude='/contributors/' \
  --exclude='/evals/' --exclude='/optional-skills/' --exclude='/ui-tui/' \
  --exclude='/nix/' --exclude='/native/' --exclude='/datagen-config-examples/' \
  --exclude='/mcp-research-data/' --exclude='/plugin-catalog/' \
  "$project_root/upstream/hermes-agent/" \
  "$runtime_bundle/upstream/hermes-agent/"
git -C "$project_root/upstream/hermes-agent" rev-parse HEAD \
  > "$runtime_bundle/upstream/hermes-agent/.jl-revision"
cp "$app_root/Runtime/run-runtime.sh" "$runtime_bundle/run-runtime.sh"
chmod 0755 "$contents/MacOS/JLAgentApp"
chmod 0755 "$runtime_bundle/run-runtime.sh"
codesign --force --sign "$signing_identity" "$app_dir"
codesign --verify --strict "$app_dir"

if [[ "$signing_identity" == "-" ]]; then
  print -u2 -- "warning: JL Agent uses ad-hoc signing; set JL_CODE_SIGN_IDENTITY to an existing Apple Development identity for stable development signing"
else
  print -u2 -- "signed JL Agent with configured identity: $signing_identity"
fi
print -r -- "$app_dir"
