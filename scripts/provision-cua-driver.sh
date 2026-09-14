#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"
release_version="0.28.0"
release_tag="cua-driver-rs-v${release_version}"
release_base="https://github.com/trycua/cua/releases/download/${release_tag}"
installer_sha256="317ba3a49fdba10f2a7f1b9f392c1bc1b7657f3aae85e1e2e43684cf17a1bf3b"
helper_sha256="c93e4415d9eeeefa92e217c46b12e9bcbffb29ea20eba5f9a80b75239926bc13"
archive_name="cua-driver-rs-${release_version}-darwin-universal.tar.gz"
archive_sha256="8130507d2a107975e665fd6308ffa1b289418aaf017b5c7ba4b1ccae524c5557"
expected_bundle_id="com.trycua.driver"
expected_teams="4YEC26S9KF YCK386LBJ7"
mode="${1:---audit}"

if [[ "$mode" != "--audit" && "$mode" != "--install" ]]; then
  print -u2 -- "usage: $0 [--audit|--install]"
  exit 64
fi
if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 -- "cua-driver provisioning is supported here only on macOS"
  exit 64
fi
for command_name in curl shasum tar codesign; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    print -u2 -- "required command is unavailable: $command_name"
    exit 69
  fi
done

audit_dir="$(mktemp -d "$project_root/.phase4c-cua.XXXXXX")"
cleanup() {
  rm -rf -- "$audit_dir"
}
trap cleanup EXIT INT TERM

download_verified() {
  local asset_name="$1"
  local expected_sha="$2"
  local destination="$audit_dir/$asset_name"
  curl --fail --show-error --silent --location \
    "$release_base/$asset_name" --output "$destination"
  local observed_sha
  observed_sha="$(shasum -a 256 "$destination" | awk '{print $1}')"
  if [[ "$observed_sha" != "$expected_sha" ]]; then
    print -u2 -- "SHA-256 mismatch for $asset_name"
    exit 65
  fi
  print -- "verified $asset_name ($observed_sha)"
}

download_verified "install.sh" "$installer_sha256"
download_verified "_install-rust.sh" "$helper_sha256"
download_verified "$archive_name" "$archive_sha256"

mkdir "$audit_dir/extracted"
tar -xzf "$audit_dir/$archive_name" -C "$audit_dir/extracted"
staged_app="$(find "$audit_dir/extracted" -type d -name CuaDriver.app -print -quit)"
if [[ -z "$staged_app" ]]; then
  print -u2 -- "verified archive does not contain CuaDriver.app"
  exit 65
fi
codesign --verify --deep --strict "$staged_app"
bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
  "$staged_app/Contents/Info.plist")"
if [[ "$bundle_id" != "$expected_bundle_id" ]]; then
  print -u2 -- "unexpected CuaDriver bundle identifier: $bundle_id"
  exit 65
fi
signature_details="$(codesign -dv --verbose=4 "$staged_app" 2>&1)"
team_id="$(print -r -- "$signature_details" | sed -n 's/^TeamIdentifier=//p' | head -1)"
if [[ " $expected_teams " != *" $team_id "* ]]; then
  print -u2 -- "unexpected CuaDriver signing team: ${team_id:-missing}"
  exit 65
fi
print -- "verified signed $bundle_id app from team $team_id"

if [[ "$mode" == "--audit" ]]; then
  print -- "audit complete; no host files were installed and no TCC prompt was requested"
  exit 0
fi

print -- "installing reviewed Cua Driver ${release_version}; this writes /Applications/CuaDriver.app and ~/.local/bin/cua-driver"
CUA_DRIVER_RS_VERSION="$release_version" \
CUA_DRIVER_RS_NO_MODIFY_PATH=1 \
CUA_DRIVER_RS_KEEP_VERSIONS=2 \
  /bin/bash "$audit_dir/install.sh" --no-modify-path

installed_app="/Applications/CuaDriver.app"
installed_binary="$installed_app/Contents/MacOS/cua-driver"
codesign --verify --deep --strict "$installed_app"
installed_bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
  "$installed_app/Contents/Info.plist")"
[[ "$installed_bundle_id" == "$expected_bundle_id" ]]
installed_signature="$(codesign -dv --verbose=4 "$installed_app" 2>&1)"
installed_team="$(print -r -- "$installed_signature" | sed -n 's/^TeamIdentifier=//p' | head -1)"
[[ " $expected_teams " == *" $installed_team "* ]]
"$installed_binary" manifest
"$installed_binary" permissions status --json || true
print -- "installation verified; TCC was not granted or bypassed"
