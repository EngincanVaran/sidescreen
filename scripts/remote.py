#!/usr/bin/env python3
"""Let Claude Code on your other Macs show up on this Mac's Sidescreen.

  python3 scripts/remote.py enable    creates a token, turns the network hook listener on (port 8766),
                                      restarts the server and prints the command for the other Mac
  python3 scripts/remote.py show      prints that command again
  python3 scripts/remote.py disable   turns the listener off (the token stays, for next time)

The listener only accepts Claude Code hook events that carry the token. The dashboard itself
stays reachable from this Mac only.
"""

import json
import re
import secrets
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
REPO_RAW = "https://raw.githubusercontent.com/EngincanVaran/sidescreen/main/scripts/hooks.py"


def load():
    if not CONFIG.exists():
        CONFIG.write_text((ROOT / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    return CONFIG.read_text(encoding="utf-8")


def set_remote(text, remote):
    """Writes the "remote" setting without reformatting the rest of config.json."""
    line = '"remote": ' + json.dumps(remote)
    if re.search(r'"remote"\s*:\s*\{[^{}]*\}', text):
        text = re.sub(r'"remote"\s*:\s*\{[^{}]*\}', line, text, count=1)
    else:
        end = text.rstrip().rfind("}")
        text = text[:end].rstrip() + ",\n\n  " + line + "\n}\n"
    json.loads(text)  # never write a broken file
    CONFIG.write_text(text, encoding="utf-8")


def host_name():
    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True).stdout.strip()
        if name:
            return name + ".local"
    except OSError:
        pass
    return socket.gethostname()


def restart():
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/com.sidescreen.server"], capture_output=True, text=True)
    print("restarted the Sidescreen server" if r.returncode == 0 else
          "restart the server yourself: launchctl kickstart -k gui/$(id -u)/com.sidescreen.server")


def show(remote):
    url = f"http://{host_name()}:{remote.get('port', 8766)}/api/hook"
    tok = remote["token"]
    print("\nOn the other Mac, run (change MacBook to the name you want on the dashboard):\n")
    print(f"curl -fsSL {REPO_RAW} -o /tmp/sidescreen-hooks.py && python3 /tmp/sidescreen-hooks.py install "
          f"--url {url} --token {tok} --machine MacBook")
    print("\nTo check the connection from the other Mac (204 = working):\n")
    print(f"curl -s -o /dev/null -w '%{{http_code}}\\n' -H 'X-Sidescreen-Token: {tok}' {url.replace('/api/hook', '/api/ping')}")
    print("\nKeep the token private: anyone on your network who has it can add sessions to the dashboard.")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "show"
    cfg = json.loads(load())
    remote = dict(cfg.get("remote") or {})
    if action == "enable":
        remote.setdefault("port", 8766)
        if len(str(remote.get("token") or "")) < 24:
            remote["token"] = secrets.token_urlsafe(32)
        remote["enabled"] = True
        set_remote(load(), remote)
        print("remote hooks enabled in config.json")
        restart()
        show(remote)
    elif action == "disable":
        remote["enabled"] = False
        set_remote(load(), remote)
        print("remote hooks disabled")
        restart()
    elif action == "show":
        if not remote.get("token"):
            raise SystemExit("not set up yet: run python3 scripts/remote.py enable")
        show(remote)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
