#!/bin/bash
# Installs Sidescreen: starts the server at login, opens the dashboard on the
# second display at login, and adds Claude Code HTTP hooks. Safe to re-run.
#
#   ./install.sh                  everything
#   ./install.sh --with-calendar  also pip-install the optional calendar libraries
#   ./install.sh --no-hooks       skip editing ~/.claude/settings.json
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs"
DOMAIN="gui/$(id -u)"
SERVER_LABEL="com.sidescreen.server"
DISPLAY_LABEL="com.sidescreen.display"

WITH_CAL=0; HOOKS=1
for a in "$@"; do
  case "$a" in
    --with-calendar) WITH_CAL=1 ;;
    --no-hooks) HOOKS=0 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 not found"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { echo "Python 3.9+ required ($("$PY" --version))"; exit 1; }
[ -d "/Applications/Google Chrome.app" ] || echo "warning: Google Chrome not found in /Applications"

# First run: start from the example settings (config.json stays out of git).
[ -f "$DIR/config.json" ] || cp "$DIR/config.example.json" "$DIR/config.json"

# Calendar reader app (macOS only shows the calendar permission prompt to apps).
if command -v swiftc >/dev/null; then
  [ -d "$DIR/build/Sidescreen Calendar.app" ] || "$DIR/scripts/calendar-helper/build.sh" || echo "warning: calendar helper didn't build; the agenda will stay empty"
else
  echo "note: swiftc not found (xcode-select --install); the agenda will only use iCal links"
fi

PORT="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port", 8765))' "$DIR/config.json" 2>/dev/null || echo 8765)"
DISPLAY_NAME="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("display", ""))' "$DIR/config.json" 2>/dev/null || true)"
chmod +x "$DIR/scripts/open-display.sh" "$DIR/uninstall.sh" 2>/dev/null || true

if [ "$WITH_CAL" = 1 ]; then
  echo "installing calendar libraries for $PY"
  "$PY" -m pip install --user --quiet icalendar recurring-ical-events
fi

mkdir -p "$AGENTS" "$LOGS"

cat > "$AGENTS/$SERVER_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$SERVER_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DIR/server.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>$LOGS/sidescreen-server.log</string>
  <key>StandardErrorPath</key><string>$LOGS/sidescreen-server.log</string>
</dict>
</plist>
EOF

cat > "$AGENTS/$DISPLAY_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$DISPLAY_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$DIR/scripts/open-display.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>SIDESCREEN_PORT</key><string>$PORT</string><key>SIDESCREEN_DISPLAY</key><string>$DISPLAY_NAME</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOGS/sidescreen-display.log</string>
  <key>StandardErrorPath</key><string>$LOGS/sidescreen-display.log</string>
</dict>
</plist>
EOF

for label in "$SERVER_LABEL" "$DISPLAY_LABEL"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
done
launchctl bootstrap "$DOMAIN" "$AGENTS/$SERVER_LABEL.plist"
echo "server agent loaded (http://127.0.0.1:$PORT)"

if [ "$HOOKS" = 1 ]; then
  "$PY" "$DIR/scripts/hooks.py" install --url "http://127.0.0.1:$PORT/api/hook"
fi

# Opening the display now (as well as at every login).
launchctl bootstrap "$DOMAIN" "$AGENTS/$DISPLAY_LABEL.plist"
echo "display agent loaded; the dashboard opens on the second screen in a few seconds"
echo "logs: $LOGS/sidescreen-*.log"
