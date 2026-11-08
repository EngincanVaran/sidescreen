#!/bin/bash
# Builds "Sidescreen Calendar.app" into build/ (the calendar reader the server runs).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
APP="$ROOT/build/Sidescreen Calendar.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
swiftc -O -o "$APP/Contents/MacOS/sidescreen-calendar" "$HERE/main.swift"
# Ad-hoc signature: macOS ties the calendar permission to it (rebuilding asks again).
codesign --force --sign - --identifier com.sidescreen.calendar "$APP" >/dev/null
echo "built $APP"
