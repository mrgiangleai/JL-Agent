#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
app_root="${script_dir:h}"
project_root="${app_root:h}"
cd "$app_root"

source "$script_dir/build-config.sh"
jl_configure_swift_build_environment "$app_root"
signing_identity="${JL_CODE_SIGN_IDENTITY:--}"
jl_validate_app_signing_identity "$signing_identity"

bin_dir="$(swift build -c release --show-bin-path)"
swift build -c release --product JLAgentApp
voice_identity="${JL_VOICE_CODE_SIGN_IDENTITY:-${JL_CODE_SIGN_IDENTITY:--}}"
JL_VOICE_CODE_SIGN_IDENTITY="$voice_identity" "$script_dir/build-voice-runtime.sh" >/dev/null
voice_app="$bin_dir/JL Voice Runtime.app"

app_dir="$bin_dir/JL Agent.app"
contents="$app_dir/Contents"
mkdir -p "$contents/MacOS" "$contents/Resources"
cp "$bin_dir/JLAgentApp" "$contents/MacOS/JLAgentApp"
cp "$app_root/Resources/Info.plist" "$contents/Info.plist"
icon_source="$app_root/Sources/JLAgentApp/Resources/JLCharacter/idle.png"
iconset="$contents/Resources/JLAgent.iconset"
if [[ ! -f "$icon_source" ]]; then
  print -u2 -- "error: JL Agent icon source is missing: $icon_source"
  exit 66
fi
rm -rf "$iconset" "$contents/Resources/JLAgent.icns"
mkdir -p "$iconset"
for spec in \
  "16:icon_16x16.png" "32:icon_16x16@2x.png" \
  "32:icon_32x32.png" "64:icon_32x32@2x.png" \
  "128:icon_128x128.png" "256:icon_128x128@2x.png" \
  "256:icon_256x256.png" "512:icon_256x256@2x.png" \
  "512:icon_512x512.png" "1024:icon_512x512@2x.png"; do
  size="${spec%%:*}"
  name="${spec#*:}"
  sips -z "$size" "$size" "$icon_source" --out "$iconset/$name" >/dev/null
done
iconutil --convert icns --output "$contents/Resources/JLAgent.icns" "$iconset"
rm -rf "$iconset"
rm -rf "$contents/Resources/JLAgent_JLAgentApp.bundle"
cp -R "$bin_dir/JLAgent_JLAgentApp.bundle" "$contents/Resources/JLAgent_JLAgentApp.bundle"
runtime_bundle="$contents/Resources/JLRuntime"
mkdir -p "$runtime_bundle/src" "$runtime_bundle/config" \
  "$runtime_bundle/upstream/hermes-agent"
rm -rf "$contents/Resources/JLVoiceRuntime.app"
cp -R "$voice_app" "$contents/Resources/JLVoiceRuntime.app"
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

root_app="$project_root/JL Agent.app"
if [[ -e "$root_app" && ! -L "$root_app" ]]; then
  print -u2 -- "error: root JL Agent.app exists but is not the managed build link: $root_app"
  exit 73
fi
relative_app="${app_dir#$project_root/}"
ln -sfn "$relative_app" "$root_app"

if [[ "$signing_identity" == "-" ]]; then
  print -u2 -- "warning: JL Agent uses ad-hoc signing; set JL_CODE_SIGN_IDENTITY to an existing Apple Development identity for stable development signing"
else
  print -u2 -- "signed JL Agent with configured identity: $signing_identity"
fi
print -r -- "$app_dir"
print -r -- "$root_app"
