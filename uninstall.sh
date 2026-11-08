#!/bin/bash
# Removes the Sidescreen LaunchAgents and Claude Code hooks.
#   ./uninstall.sh          keep the dashboard's Chrome profile (~/.sidescreen-chrome)
#   ./uninstall.sh --purge  also delete that profile
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
PY="$(command -v python3 || echo /usr/bin/python3)"
PORT="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port", 8765))' "$DIR/config.json" 2>/dev/null || echo 8765)"

for label in com.sidescreen.server com.sidescreen.display; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null && echo "stopped $label" || true
  rm -f "$AGENTS/$label.plist"
done

"$PY" "$DIR/scripts/hooks.py" uninstall --url "http://127.0.0.1:$PORT/api/hook"

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$HOME/.sidescreen-chrome"
  echo "removed ~/.sidescreen-chrome"
fi
echo "done. Close the dashboard window yourself if it's still open."
