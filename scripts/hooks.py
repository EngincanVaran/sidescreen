#!/usr/bin/env python3
"""Add or remove the Sidescreen HTTP hooks in Claude Code's settings.json.

  python3 scripts/hooks.py install   [--settings PATH] [--url URL] [--token T --machine NAME]
  python3 scripts/hooks.py uninstall [--settings PATH] [--url URL]

To send this Mac's sessions to Sidescreen on another Mac (see scripts/remote.py on that Mac):
  python3 scripts/hooks.py install --url http://Other-Mac.local:8766/api/hook --token T --machine MacBook

Backs the file up before writing, keeps every other setting and hook as-is,
and never adds the same hook twice.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

EVENTS = [
    ("SessionStart", None),
    ("UserPromptSubmit", None),
    ("PreToolUse", "*"),
    ("PostToolUse", "*"),
    ("PermissionRequest", None),
    ("Notification", None),
    ("Stop", None),
    ("StopFailure", None),
    ("SessionEnd", None),
]


def is_ours(hook, url):
    return isinstance(hook, dict) and hook.get("type") == "http" and hook.get("url") == url


def make_hook(url, token=None, machine=None):
    hook = {"type": "http", "url": url, "timeout": 2}
    if token:
        # Another Mac: send the shared token and a name for the dashboard; give up fast when it's unreachable.
        hook["headers"] = {"X-Sidescreen-Token": token, "X-Sidescreen-Machine": machine or "Remote"}
        hook["timeout"] = 1
    return hook


def install(settings, url, token=None, machine=None):
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit("settings.json: 'hooks' is not an object, refusing to edit it")
    added = 0
    for event, matcher in EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise SystemExit(f"settings.json: hooks.{event} is not a list, refusing to edit it")
        want = make_hook(url, token, machine)
        mine = [h for g in groups if isinstance(g, dict) for h in g.get("hooks") or [] if is_ours(h, url)]
        if mine:
            # Already there: refresh the token / name if they changed.
            for h in mine:
                if h != want:
                    h.clear(); h.update(want); added += 1
            continue
        group = {"hooks": [want]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        groups.append(group)
        added += 1
    return added


def uninstall(settings, url):
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                before = len(g["hooks"])
                g["hooks"] = [h for h in g["hooks"] if not is_ours(h, url)]
                removed += before - len(g["hooks"])
                if not g["hooks"]:
                    continue
            kept.append(g)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["install", "uninstall"])
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ap.add_argument("--url", default="http://127.0.0.1:8765/api/hook")
    ap.add_argument("--token", help="shared token, for sending to Sidescreen on another Mac")
    ap.add_argument("--machine", default="", help="name shown on the dashboard, e.g. MacBook")
    args = ap.parse_args()

    path = Path(args.settings).expanduser()
    if path.exists():
        text = path.read_text(encoding="utf-8")
        try:
            settings = json.loads(text) if text.strip() else {}
        except ValueError as e:
            raise SystemExit(f"{path} is not valid JSON ({e}); fix it first, nothing was changed")
        if not isinstance(settings, dict):
            raise SystemExit(f"{path} is not a JSON object; nothing was changed")
    elif args.action == "uninstall":
        print(f"{path} does not exist, nothing to remove")
        return
    else:
        settings = {}

    n = install(settings, args.url, args.token, args.machine) if args.action == "install" else uninstall(settings, args.url)
    if n == 0:
        print(f"hooks already {'present' if args.action == 'install' else 'absent'} in {path}, no change")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-sidescreen-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"backed up {path} -> {backup}")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".settings.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    print(f"{'added' if args.action == 'install' else 'removed'} {n} hook(s) in {path}")


if __name__ == "__main__":
    sys.exit(main())
