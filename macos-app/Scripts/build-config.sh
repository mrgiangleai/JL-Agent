#!/bin/zsh

# Shared, explicit build configuration for the installed Command Line Tools
# Swift compiler. This file is sourced by the app and voice-host build lanes.

jl_configure_swift_build_environment() {
  local build_root="$1"
  local swift_sdk="${JL_SWIFT_SDKROOT:-/Library/Developer/CommandLineTools/SDKs/MacOSX15.5.sdk}"
  local module_cache="$build_root/.build/ModuleCache"

  if [[ ! -d "$swift_sdk" ]]; then
    print -u2 -- "error: supported Swift SDK is missing: $swift_sdk"
    return 69
  fi

  mkdir -p "$module_cache"
  export SDKROOT="$swift_sdk"
  export CLANG_MODULE_CACHE_PATH="$module_cache"
  export SWIFTPM_MODULECACHE_OVERRIDE="$module_cache"
}

jl_validate_app_signing_identity() {
  local signing_identity="$1"
  if [[ "$signing_identity" == "-" ]]; then
    return 0
  fi

  case "$signing_identity" in
    "Apple Development: "*) ;;
    *)
      print -u2 -- "error: JL Agent stable signing requires an Apple Development identity"
      return 64
      ;;
  esac

  if ! security find-identity -v -p codesigning | grep -Fq -- "\"$signing_identity\""; then
    print -u2 -- "error: requested signing identity is not valid in the current keychains: $signing_identity"
    return 69
  fi
}

jl_validate_voice_signing_identity() {
  local signing_identity="$1"
  if [[ "$signing_identity" == "-" ]]; then
    return 0
  fi
  if [[ -z "$signing_identity" ]]; then
    print -u2 -- "error: JL_VOICE_CODE_SIGN_IDENTITY must name an Apple Development identity"
    return 64
  fi

  case "$signing_identity" in
    "Apple Development: "*) ;;
    *)
      print -u2 -- "error: JL voice runtime signing requires an Apple Development identity"
      return 64
      ;;
  esac

  if ! security find-identity -v -p codesigning | grep -Fq -- "\"$signing_identity\""; then
    print -u2 -- "error: requested signing identity is not valid in the current keychains: $signing_identity"
    return 69
  fi
}
