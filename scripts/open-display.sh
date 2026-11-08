#!/bin/bash
# Opens the dashboard as a fullscreen Chrome app window on the second display.
# Run by the com.sidescreen.display LaunchAgent at login; safe to run by hand too.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${SIDESCREEN_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

sleep "${SIDESCREEN_DELAY:-8}"

# Wait up to 60 s for the server (it starts at login too).
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null "$URL/api/health" && break
  sleep 1
done

FRAME="$(osascript -l JavaScript "$DIR/screen-frame.js" "${SIDESCREEN_DISPLAY:-}" 2>/dev/null)"
IFS=, read -r X Y W H COUNT NAME <<<"${FRAME:-0,0,1920,1080,1,main}"
echo "$(date '+%F %T') opening on ${NAME:-?} at $X,$Y ${W}x$H ($COUNT displays)"

exec open -na "Google Chrome" --args \
  --app="$URL" \
  --user-data-dir="$HOME/.sidescreen-chrome" \
  --window-position="$X,$Y" \
  --window-size="$W,$H" \
  --start-fullscreen \
  --no-first-run --no-default-browser-check \
  --autoplay-policy=no-user-gesture-required
