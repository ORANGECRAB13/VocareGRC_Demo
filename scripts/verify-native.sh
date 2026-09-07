#!/usr/bin/env bash
# Verify the native iOS and Android apps under native/ plus the backend contract.
#
#   scripts/verify-native.sh                 # contract tests (in-process) + iOS + Android
#   VOCARE_API_BASE=https://... scripts/verify-native.sh   # also run the live contract tests
#
# Each stage that cannot run on this machine prints a clear SKIP and is not a
# failure. Any stage that is attempted and fails makes the script exit non-zero.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAILED=0
IOS_SCHEME="${IOS_SCHEME:-VocaApp}"
IOS_DESTINATION="${IOS_DESTINATION:-platform=iOS Simulator,name=iPhone 16}"

section() { printf '\n==> %s\n' "$*"; }
fail()    { printf 'FAIL: %s\n' "$*"; FAILED=1; }
skip()    { printf 'SKIP: %s\n' "$*"; }

# ---------------------------------------------------------------- 1. Python contract tests
section "Backend contract tests (tests/test_native_api_contract.py)"
if [ -n "${VOCARE_API_BASE:-}" ]; then
  echo "VOCARE_API_BASE is set: live tests against $VOCARE_API_BASE will run too"
else
  echo "VOCARE_API_BASE unset: live tests skipped, in-process tests against bot.py only"
fi
if python3 -m unittest tests.test_native_api_contract tests.test_entitlement_lifecycle tests.test_store_entitlements; then
  echo "contract tests passed"
else
  fail "contract tests"
fi

# ---------------------------------------------------------------- 2. iOS
section "iOS (native/ios)"
IOS_DIR="$ROOT/native/ios"
IOS_PROJECT="$(ls -d "$IOS_DIR"/*.xcodeproj 2>/dev/null | head -n1 || true)"
IOS_WORKSPACE="$(ls -d "$IOS_DIR"/*.xcworkspace 2>/dev/null | head -n1 || true)"
if [ ! -d "$IOS_DIR" ]; then
  skip "native/ios does not exist"
elif ! command -v xcodebuild >/dev/null 2>&1; then
  skip "xcodebuild not found (needs macOS with Xcode)"
elif [ -z "$IOS_PROJECT" ] && [ -z "$IOS_WORKSPACE" ]; then
  skip "no .xcodeproj/.xcworkspace under native/ios yet"
else
  if [ -n "$IOS_WORKSPACE" ]; then CONTAINER=(-workspace "$IOS_WORKSPACE"); else CONTAINER=(-project "$IOS_PROJECT"); fi
  echo "container: ${CONTAINER[1]}  scheme: $IOS_SCHEME  destination: $IOS_DESTINATION"
  if xcodebuild "${CONTAINER[@]}" -scheme "$IOS_SCHEME" -destination "$IOS_DESTINATION" \
       CODE_SIGNING_ALLOWED=NO -quiet build; then
    echo "iOS build passed"
    if xcodebuild "${CONTAINER[@]}" -scheme "$IOS_SCHEME" -destination "$IOS_DESTINATION" \
         CODE_SIGNING_ALLOWED=NO -quiet test; then
      echo "iOS tests passed"
    else
      fail "iOS tests (xcodebuild test)"
    fi
  else
    fail "iOS build (xcodebuild build)"
  fi
  # The Swift package can also be tested on its own when it exists.
  if [ -f "$IOS_DIR/VocaKit/Package.swift" ] && command -v swift >/dev/null 2>&1; then
    echo "note: VocaKit package tests are covered by the app scheme; run 'swift test' in native/ios/VocaKit for a macOS-only quick check if the package has no iOS-only deps"
  fi
fi

# ---------------------------------------------------------------- 3. Android
section "Android (native/android)"
ANDROID_DIR="$ROOT/native/android"
if [ ! -d "$ANDROID_DIR" ] || [ -z "$(ls -A "$ANDROID_DIR" 2>/dev/null)" ]; then
  skip "native/android does not exist or is empty"
elif [ ! -f "$ANDROID_DIR/gradlew" ]; then
  skip "native/android/gradlew missing (project not scaffolded yet)"
elif ! java -version >/dev/null 2>&1; then
  # macOS ships a /usr/bin/java stub that fails when no runtime is installed.
  skip "no working JDK (java -version failed; install a JDK 21 to run Gradle)"
elif [ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ] && [ ! -f "$ANDROID_DIR/local.properties" ]; then
  skip "no Android SDK (set ANDROID_HOME/ANDROID_SDK_ROOT or add native/android/local.properties)"
else
  if (cd "$ANDROID_DIR" && bash gradlew --no-daemon assembleDebug testDebugUnitTest); then
    echo "Android build + unit tests passed"
  else
    fail "Android (gradlew assembleDebug testDebugUnitTest)"
  fi
fi

# ---------------------------------------------------------------- summary
section "Summary"
if [ "$FAILED" -ne 0 ]; then
  echo "verify-native: FAILED"
  exit 1
fi
echo "verify-native: OK (skipped stages are listed above)"
