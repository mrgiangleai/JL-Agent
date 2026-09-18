#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
app_root="${script_dir:h}"
cd "$app_root"

source "$script_dir/build-config.sh"
jl_configure_swift_build_environment "$app_root"

signing_identity="${JL_VOICE_CODE_SIGN_IDENTITY:--}"
jl_validate_voice_signing_identity "$signing_identity"

bin_dir="$(swift build -c release --show-bin-path)"
swift build -c release --product JLVoiceRuntime

app_dir="$bin_dir/JL Voice Runtime.app"
contents="$app_dir/Contents"
rm -rf "$app_dir"
rm -rf "$contents/Resources/JLRuntime"
mkdir -p "$contents/MacOS" "$contents/Resources/JLRuntime/src" \
  "$contents/Resources/JLRuntime/config" \
  "$contents/Resources/JLRuntime/upstream/hermes-agent" \
  "$contents/Resources/JLRuntime/python"
cp "$bin_dir/JLVoiceRuntime" "$contents/MacOS/JLVoiceRuntime"
cp "$app_root/VoiceRuntime/Info.plist" "$contents/Info.plist"
rsync -a --delete \
  --exclude='__pycache__/' --exclude='*.py[cod]' \
  "$app_root/../src/" "$contents/Resources/JLRuntime/src/"
rsync -a --delete \
  --exclude='local*' --exclude='*.key' --exclude='*.pem' \
  "$app_root/../config/" "$contents/Resources/JLRuntime/config/"
rsync -a --delete \
  --exclude='.git' --exclude='__pycache__/' --exclude='*.py[cod]' \
  --exclude='/tests/' --exclude='/tests-js/' --exclude='/apps/' \
  --exclude='/website/' --exclude='/docs/' --exclude='/contributors/' \
  --exclude='/evals/' --exclude='/optional-skills/' --exclude='/ui-tui/' \
  --exclude='/nix/' --exclude='/native/' --exclude='/datagen-config-examples/' \
  --exclude='/mcp-research-data/' --exclude='/plugin-catalog/' \
  "$app_root/../upstream/hermes-agent/" \
  "$contents/Resources/JLRuntime/upstream/hermes-agent/"
git -C "$app_root/../upstream/hermes-agent" rev-parse HEAD \
  > "$contents/Resources/JLRuntime/upstream/hermes-agent/.jl-revision"
cp "$app_root/Runtime/run-runtime.sh" "$contents/Resources/JLRuntime/run-runtime.sh"
chmod 0755 "$contents/Resources/JLRuntime/run-runtime.sh"
chmod 0755 "$contents/MacOS/JLVoiceRuntime"

codesign \
  --force \
  --options runtime \
  --timestamp=none \
  --entitlements "$app_root/VoiceRuntime/JLVoiceRuntime.entitlements" \
  --identifier com.jlagent.voice-runtime \
  --sign "$signing_identity" \
  "$app_dir"

codesign --verify --deep --strict --verbose=2 "$app_dir"

signature_details="$(codesign -dv --verbose=4 "$app_dir" 2>&1)"
if [[ "$signature_details" != *"Identifier=com.jlagent.voice-runtime"* ]]; then
  print -u2 -- "error: signed bundle has the wrong code identifier"
  exit 70
fi
if [[ "$signing_identity" != "-" && "$signature_details" == *"TeamIdentifier=not set"* ]]; then
  print -u2 -- "error: signed bundle has no stable Apple TeamIdentifier"
  exit 70
fi


entitlements_xml="$(codesign -d --entitlements :- "$app_dir" 2>&1 | sed -n '/<?xml/,$p')"
if [[ "$entitlements_xml" != *"<key>com.apple.security.device.audio-input</key><true/>"* ]]; then
  print -u2 -- "error: signed bundle is missing the audio-input entitlement"
  exit 70
fi

usage_description="$(plutil -extract NSMicrophoneUsageDescription raw -o - "$contents/Info.plist")"
if [[ -z "$usage_description" ]]; then
  print -u2 -- "error: signed bundle is missing NSMicrophoneUsageDescription"
  exit 70
fi

codesign -dr - --verbose=2 "$app_dir" 2>&1
if [[ "$signing_identity" == "-" ]]; then
  print -u2 -- "warning: JL Voice Runtime uses ad-hoc signing; Microphone permission may need regrant after rebuilds"
fi
print -r -- "$app_dir"
