#!/usr/bin/env python3
"""Sidescreen local server.

Standard library only (Python 3.9+). Serves the dashboard and keeps track of
Claude Code sessions from HTTP hook events. Calendar support is optional and
needs `icalendar` + `recurring-ical-events`.
"""

import gzip
import hmac
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import html
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CONFIG_PATH = Path(os.environ.get("SIDESCREEN_CONFIG", ROOT / "config.json"))

DEFAULT_CONFIG = {
    "port": 8765,
    "location": {"name": "Istanbul", "lat": 41.01, "lon": 28.98},
    "timezone": "Europe/Istanbul",
    "locale": "en-GB",
    "hour12": False,
    "calendars": [],
    "calendar_days_ahead": 2,
    "night_dim": {"start": "00:30", "end": "07:30", "opacity": 0.45},
    "markets": ["USD", "EUR", "GRA"],
    "crypto": ["bitcoin", "ethereum"],
    "stocks": {"api_key": "", "symbols": []},
    "reminders": {"enabled": False, "lists": [], "days_ahead": 7},
    "football": ["eng.1", "tur.1", "esp.1", "uefa.champions", "uefa.europa", "uefa.europa.conf"],
    "tennis": ["atp", "wta"],
    "basketball": ["E", "U"],
    "night_clock": "off",
    "news": [
        {"name": "World", "feeds": ["https://feeds.bbci.co.uk/news/world/rss.xml",
                                    "https://rss.dw.com/rdf/rss-en-world"]},
        {"name": "Türkiye", "feeds": ["https://feeds.bbci.co.uk/turkce/rss.xml",
                                      "https://www.ntv.com.tr/gundem.rss",
                                      "https://rss.dw.com/rdf/rss-tur-all"],
         "exclude": ["deprem mi oldu"]},
    ],
    "rotate_seconds": 12,
}
PUBLIC_KEYS = ("location", "timezone", "locale", "hour12", "night_dim", "night_clock", "rotate_seconds", "kickoff_reminder_minutes", "goal_alerts")
STARTED = time.time()


def version():
    """Changes when the server restarts or the page file changes, so open dashboards reload."""
    try:
        mtime = (STATIC / "index.html").stat().st_mtime
    except OSError:
        mtime = 0
    return f"{int(STARTED)}-{int(mtime)}"
PREFS_PATH = Path(os.environ.get("SIDESCREEN_PREFS", ROOT / "prefs.json"))

STALE_AFTER = 8 * 3600
CALENDAR_TTL = 5 * 60
STATE_ORDER = {"waiting": 0, "error": 1, "working": 2, "done": 3, "idle": 4}
WAITING_NOTIFICATIONS = {
    "permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log(f"config.json unreadable, using defaults: {e}")
    return cfg


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


def snippet(text, limit=140):
    if not isinstance(text, str):
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------- sessions

SESSIONS_PATH = Path(os.environ.get("SIDESCREEN_SESSIONS", ROOT / "sessions.json"))


class Sessions:
    """Session states, saved to sessions.json so a server restart doesn't forget a waiting session."""

    def __init__(self):
        self._lock = threading.Lock()
        self._s = {}
        self._dirty = False
        try:
            data = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
            cutoff = time.time() - STALE_AFTER
            self._s = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("updated", 0) > cutoff}
        except (OSError, ValueError):
            pass
        threading.Thread(target=self._saver, daemon=True).start()

    def _saver(self):
        while True:
            time.sleep(2)
            if not self._dirty:
                continue
            with self._lock:
                data, self._dirty = json.dumps(self._s, ensure_ascii=False), False
            try:
                tmp = SESSIONS_PATH.with_suffix(".tmp")
                tmp.write_text(data, encoding="utf-8")
                os.replace(tmp, SESSIONS_PATH)
            except OSError as e:
                log(f"could not save sessions: {e}")

    def handle(self, p, machine=""):
        if not isinstance(p, dict):
            return
        sid = p.get("session_id")
        event = p.get("hook_event_name")
        if not sid or not event:
            return
        now = time.time()
        with self._lock:
            self._dirty = True
            if event == "SessionEnd":
                self._s.pop(sid, None)
                return
            s = self._s.get(sid)
            if s is None:
                s = self._s[sid] = {
                    "id": sid, "project": "", "state": "idle", "tool": None, "detail": "",
                    "prompt": "", "message": "", "tools": 0, "turn_started": None, "updated": now,
                }
            if machine:
                s["machine"] = machine  # sessions from another Mac (see RemoteHandler)
            cwd = p.get("cwd")
            if cwd:
                s["project"] = os.path.basename(cwd.rstrip("/")) or cwd
            s["updated"] = now
            apply_event(s, event, p, now)


    def snapshot(self):
        cutoff = time.time() - STALE_AFTER
        with self._lock:
            for sid in [k for k, v in self._s.items() if v["updated"] < cutoff]:
                del self._s[sid]
            items = [dict(v) for v in self._s.values()]
        items.sort(key=lambda s: (STATE_ORDER.get(s["state"], 9), -s["updated"]))
        return items


ASKING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


def asking_detail(tool, ti):
    ti = ti if isinstance(ti, dict) else {}
    if tool == "ExitPlanMode":
        return "Plan ready for your review"
    qs = ti.get("questions")
    if isinstance(qs, list) and qs and isinstance(qs[0], dict) and qs[0].get("question"):
        return "Question: " + snippet(qs[0]["question"], 120)
    return "Question for you"


def ending_question(msg):
    """The question in the last paragraph of a reply, if the reply ends by asking one."""
    paras = [x for x in re.split(r"\n\s*\n", (msg or "").strip()) if x.strip()]
    if not paras:
        return ""
    last = re.sub(r"[*_`#>]+", "", paras[-1])
    if "?" not in last:
        return ""
    qs = re.findall(r"[^.!?\n]*\?", last)
    q = qs[-1].strip(" -:") if qs else ""
    return snippet(q, 140) if len(q) > 3 else ""


def tool_detail(tool, ti):
    if not isinstance(ti, dict):
        return ""
    if ti.get("command"):
        return snippet(ti["command"], 120)
    for key in ("file_path", "notebook_path", "path"):
        if ti.get(key):
            return os.path.basename(str(ti[key]).rstrip("/"))
    for key in ("pattern", "url", "query", "description"):
        if ti.get(key):
            return snippet(str(ti[key]), 120)
    return ""


def apply_event(s, event, p, now):
    if event == "SessionStart":
        s.update(state="idle", tool=None, detail="")
    elif event == "UserPromptSubmit":
        s.update(state="working", prompt=snippet(p.get("prompt")), tool=None, detail="",
                 message="", tools=0, turn_started=now)
    elif event == "PreToolUse" and p.get("tool_name") in ASKING_TOOLS:
        # Claude is putting a question or a plan in front of you: that's "needs you", not "working".
        s.update(state="waiting", tool=None, detail=asking_detail(p.get("tool_name"), p.get("tool_input")))
        s["tools"] += 1
    elif event == "PreToolUse":
        tool = p.get("tool_name") or "Tool"
        s.update(state="working", tool=tool, detail=tool_detail(tool, p.get("tool_input")))
        s["tools"] += 1
        if not s["turn_started"]:
            s["turn_started"] = now
    elif event == "PostToolUse":
        if s["state"] == "waiting":
            s["state"] = "working"
    elif event == "PermissionRequest":
        tool = p.get("tool_name") or "tool"
        detail = tool_detail(tool, p.get("tool_input"))
        s.update(state="waiting", detail=f"Permission: {tool}" + (f"  {detail}" if detail else ""))
    elif event == "Notification":
        kind = p.get("notification_type")
        if kind in WAITING_NOTIFICATIONS:
            s.update(state="waiting", detail=snippet(p.get("message")) or "Waiting for input")
        elif kind == "idle_prompt" and s["state"] not in ("done", "error"):
            s.update(state="idle", tool=None, detail="")
    elif event == "Stop":
        msg = p.get("last_assistant_message") or ""
        q = ending_question(msg)
        # A turn that ends by asking you something is waiting on you, not finished.
        s.update(state="waiting" if q else "done", tool=None, detail=q or "", message=snippet(msg))
    elif event == "StopFailure":
        err = p.get("error") or p.get("error_message") or p.get("reason")
        s.update(state="error", tool=None, detail=snippet(err if isinstance(err, str) else "") or "Turn failed")


SESSIONS = Sessions()


# ---------------------------------------------------------------- Claude Code usage

class Usage:
    """Today's Claude Code token use, read from Claude Code's own transcripts (~/.claude/projects/**/*.jsonl).
    Reads only files touched today, and only the part added since the last look."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pos = {}    # file -> bytes already read
        self._msgs = {}   # message id -> (time, project, session, input, cache write, cache read, output)
        self._day = None
        self._at = 0.0
        self._cache = None

    def get(self, cfg):
        tz = ZoneInfo(cfg.get("timezone") or "UTC")
        now = datetime.now(tz)
        day = now.strftime("%Y-%m-%d")
        with self._lock:
            if day != self._day:
                self._day, self._pos, self._msgs, self._cache = day, {}, {}, None
            if self._cache is None or time.time() - self._at > 30:
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                try:
                    self._scan(start.timestamp())
                except Exception as e:
                    log(f"usage: {e}")
                self._cache = self._summary(tz)
                self._at = time.time()
            return self._cache

    def _scan(self, since):
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
        if not root.is_dir():
            return
        for f in root.rglob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mtime < since:
                continue
            pos = self._pos.get(f, 0)
            if st.st_size < pos:
                pos = 0
            if st.st_size == pos:
                continue
            with open(f, "rb") as fh:
                fh.seek(pos)
                data = fh.read()
            end = data.rfind(b"\n") + 1  # leave a half-written last line for next time
            self._pos[f] = pos + end
            for line in data[:end].splitlines():
                if b'"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                m = d.get("message")
                if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
                    continue
                t = parse_iso(d.get("timestamp") or "")
                if t is None or t < since:
                    continue
                u = m["usage"]
                n = lambda k: int(u.get(k) or 0)
                # The same reply is logged once per content block: keep one copy per message id.
                key = m.get("id") or d.get("requestId") or d.get("uuid")
                project = Path(d.get("cwd") or "").name or f.parent.name
                self._msgs[key] = (t, project, d.get("sessionId") or f.stem, n("input_tokens"),
                                   n("cache_creation_input_tokens"), n("cache_read_input_tokens"), n("output_tokens"))

    def _summary(self, tz):
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        hourly = [0] * 24
        projects, sessions = {}, {}
        # A session belongs to the folder it started in (even after it cd's into a subfolder).
        first = {}
        for t, project, sess, *_ in self._msgs.values():
            if sess not in first or t < first[sess][0]:
                first[sess] = (t, project)
        for t, _, sess, i, cw, cr, o in self._msgs.values():
            project = first[sess][1]
            tot["input"] += i; tot["cache_write"] += cw; tot["cache_read"] += cr; tot["output"] += o
            all_ = i + cw + cr + o
            hourly[datetime.fromtimestamp(t, tz).hour] += all_
            p = projects.setdefault(project, {"name": project, "total": 0, "output": 0, "messages": 0})
            p["total"] += all_; p["output"] += o; p["messages"] += 1
            sessions.setdefault(sess, []).append(t)
        # Active time: replies less than 5 minutes apart count as continuous work.
        active = 0
        for ts in sessions.values():
            ts.sort()
            active += sum(min(b - a, 300) for a, b in zip(ts, ts[1:]))
        return {"date": self._day, "total": sum(tot.values()), **tot, "messages": len(self._msgs),
                "sessions": len(sessions), "active_min": round(active / 60), "hourly": hourly,
                "projects": sorted(projects.values(), key=lambda p: -p["total"])[:6]}


USAGE = Usage()


# ---------------------------------------------------------------- calendar

class Calendar:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache = None
        self._at = 0.0

    def get(self, cfg):
        apple = APPLE_CAL.data
        ical = self._ical(cfg)
        if not apple.get("enabled"):
            return ical
        events = list(apple.get("events") or [])
        if ical.get("enabled"):
            events += ical.get("events") or []
        events.sort(key=lambda e: e["start"])
        out = {"enabled": True, "events": events, "errors": ical.get("errors", 0)}
        if apple.get("error"):
            out["apple_error"] = apple["error"]
        return out

    def _ical(self, cfg):
        urls = [u for u in cfg.get("calendars") or [] if isinstance(u, str) and u.strip()]
        if not urls:
            return {"enabled": False, "reason": "no calendars configured"}
        try:
            import icalendar  # noqa: F401
            import recurring_ical_events  # noqa: F401
        except ImportError:
            return {"enabled": False, "reason": "icalendar / recurring-ical-events not installed"}
        with self._lock:
            if self._cache is None or time.time() - self._at > CALENDAR_TTL:
                self._cache = self._fetch(cfg, urls)
                self._at = time.time()
            return self._cache

    def _fetch(self, cfg, urls):
        import icalendar
        import recurring_ical_events

        tz = ZoneInfo(cfg.get("timezone") or "UTC")
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=max(1, int(cfg.get("calendar_days_ahead") or 2)))
        events, errors = [], 0
        for url in urls:
            url = url.strip()
            if url.startswith("webcal://"):
                url = "https://" + url[len("webcal://"):]
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "sidescreen/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    cal = icalendar.Calendar.from_ical(r.read())
                for ev in recurring_ical_events.of(cal).between(start, end):
                    item = to_event(ev, tz)
                    if item:
                        events.append(item)
            except Exception as e:  # one bad feed shouldn't hide the others
                errors += 1
                log(f"calendar fetch failed ({type(e).__name__}): {e}")
        events.sort(key=lambda e: e["start"])
        return {"enabled": True, "events": events, "errors": errors}


class AppleCalendar:
    """Reads the Mac's own calendars (EventKit) in the background, every 5 minutes."""

    def __init__(self):
        self.data = {"enabled": False}

    def start(self, cfg):
        ac = cfg.get("apple_calendar") or {}
        if not ac.get("enabled", True):
            return
        self.data = {"enabled": True, "events": [], "loading": True}
        threading.Thread(target=self._loop, args=(cfg, ac), daemon=True).start()

    APP = ROOT / "build" / "Sidescreen Calendar.app"

    def _read(self, days, names):
        """Runs the helper app through LaunchServices (`open`), so macOS asks for calendar access
        on behalf of "Sidescreen Calendar" (a plain script can't get it)."""
        if not self.APP.exists():
            raise RuntimeError("calendar helper not built: run ./install.sh (or scripts/calendar-helper/build.sh)")
        out = ROOT / "build" / "calendar-out.json"
        out.unlink(missing_ok=True)
        r = subprocess.run(["open", "-W", "-n", "-g", "--stdout", str(out), "--stderr", "/dev/null",
                            "-a", str(self.APP), "--args", days, names], capture_output=True, text=True, timeout=240)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "open failed").strip()[:200])
        return json.loads(out.read_text() or "{}")

    def _loop(self, cfg, ac):
        names = json.dumps([x for x in ac.get("calendars") or [] if isinstance(x, str)])
        days = str(max(1, int(cfg.get("calendar_days_ahead") or 2)))
        while True:
            try:
                d = self._read(days, names)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                self.data = {"enabled": True, "events": d.get("events") or [], "updated": time.time()}
            except Exception as e:
                log(f"apple calendar: {e}")
                self.data = {"enabled": True, "events": (self.data.get("events") or []), "error": str(e)[:200]}
            time.sleep(300)


APPLE_CAL = AppleCalendar()


def to_event(ev, tz):
    if str(ev.get("STATUS", "")).upper() == "CANCELLED":
        return None
    s, e = ev.get("DTSTART"), ev.get("DTEND")
    if s is None:
        return None
    s = s.dt
    e = e.dt if e is not None else s
    all_day = isinstance(s, date) and not isinstance(s, datetime)

    def norm(d):
        if not isinstance(d, datetime):
            d = datetime(d.year, d.month, d.day)
        return d.replace(tzinfo=tz) if d.tzinfo is None else d.astimezone(tz)

    return {
        "title": str(ev.get("SUMMARY", "")).strip() or "(no title)",
        "location": str(ev.get("LOCATION", "")).strip(),
        "start": norm(s).isoformat(),
        "end": norm(e).isoformat(),
        "all_day": all_day,
    }


CALENDAR = Calendar()

# ---------------------------------------------------------------- markets + football

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) sidescreen/1.0"
MARKETS_URL = "https://finans.truncgil.com/v4/today.json"
ESPN = "https://site.api.espn.com/apis"
MARKET_NAMES = {
    "USD": "US Dollar", "EUR": "Euro", "GBP": "British Pound", "CHF": "Swiss Franc",
    "GRA": "Gram gold", "CEYREKALTIN": "Quarter gold", "YARIMALTIN": "Half gold",
    "TAMALTIN": "Full gold", "ONS": "Gold ounce", "GUMUS": "Silver (gram)",
}


LEAGUE_NAMES = {"uefa.europa.conf": "Conference League", "eng.1": "Premier League", "tur.1": "Süper Lig", "esp.1": "La Liga",
                "ita.1": "Serie A", "ger.1": "Bundesliga", "fra.1": "Ligue 1",
                "uefa.champions": "Champions League", "uefa.europa": "Europa League"}


class _Redirect308(urllib.request.HTTPRedirectHandler):
    """Python 3.9's urllib doesn't follow 308 redirects; treat them like 307."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code == 308:
            code = 307
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_307


OPENER = urllib.request.build_opener(_Redirect308)


def get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Cache:
    """Per-key TTL cache that keeps serving the last good value when a fetch fails."""

    def __init__(self):
        self._lock = threading.Lock()
        self._locks = {}
        self._data = {}
        self._failed = {}

    def get(self, key, ttl, fn):
        with self._lock:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            hit = self._data.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            if not hit and time.time() - self._failed.get(key, 0) < 300:
                raise RuntimeError(f"{key}: waiting before retrying")  # don't hammer a source that just refused us
            try:
                val = fn()
            except Exception as e:
                log(f"{key} fetch failed ({type(e).__name__}): {e}")
                self._failed[key] = time.time()
                if hit:  # retry in a minute, serve stale meanwhile
                    self._data[key] = (time.time() - ttl + 60, hit[1])
                    return hit[1]
                raise
            self._data[key] = (time.time(), val)
            return val


CACHE = Cache()


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def fetch_markets(codes):
    d = get_json(MARKETS_URL)
    items = []
    for code in codes:
        row = d.get(code)
        if not isinstance(row, dict):
            continue
        items.append({
            "code": code,
            "name": MARKET_NAMES.get(code) or str(row.get("Name") or code).title(),
            "kind": "gold" if row.get("Type") == "Gold" else "currency",
            "buy": num(row.get("Buying")),
            "sell": num(row.get("Selling")),
            "change": num(row.get("Change")),
        })
    return {"items": items, "updated": d.get("Update_Date"), "source": "truncgil"}


def fetch_standings(league):
    d = get_json(f"{ESPN}/v2/sports/soccer/{league}/standings")
    child = (d.get("children") or [{}])[0]
    rows = []
    for e in (child.get("standings") or {}).get("entries") or []:
        st = {x.get("name"): x for x in e.get("stats") or []}
        val = lambda k: (st.get(k) or {}).get("value")
        team = e.get("team") or {}
        rows.append({
            "rank": int(val("rank") or 0),
            "id": str(team.get("id") or ""),
            "logo": small_logo(((team.get("logos") or [{}])[0]).get("href"), 40),
            "full": team.get("displayName") or "",
            "team": team.get("shortDisplayName") or team.get("displayName") or "",
            "abbr": team.get("abbreviation") or "",
            "played": int(val("gamesPlayed") or 0),
            "w": int(val("wins") or 0),
            "d": int(val("ties") or 0),
            "l": int(val("losses") or 0),
            "gd": int(val("pointDifferential") or 0),
            "pts": int(val("points") or 0),
        })
    rows.sort(key=lambda r: r["rank"] or 99)
    return {"name": d.get("abbreviation") or d.get("name") or league, "season": child.get("name"), "table": rows}


def small_logo(href, size=80):
    """ESPN serves 500px logos; ask its resizer for a small one."""
    m = re.match(r"https://a\.espncdn\.com(/i/.+\.png)$", href or "")
    return f"https://a.espncdn.com/combiner/i?img={m.group(1)}&w={size}&h={size}" if m else href


def league_logo(d):
    logos = ((d.get("leagues") or [{}])[0]).get("logos") or []
    pick = next((l for l in logos if "dark" in (l.get("rel") or [])), logos[0] if logos else None)
    return small_logo(pick.get("href")) if pick else None


def fetch_scores(league):
    d = get_json(f"{ESPN}/site/v2/sports/soccer/{league}/scoreboard")
    matches = []
    for ev in d.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        st = ((ev.get("status") or {}).get("type")) or {}
        sides = {}
        for c in comp.get("competitors") or []:
            t = c.get("team") or {}
            sides[c.get("homeAway")] = {
                "id": str(t.get("id") or ""),
                "team": t.get("shortDisplayName") or t.get("displayName") or "",
                "abbr": t.get("abbreviation") or "",
                "score": c.get("score"),
            }
        goals = []
        for x in comp.get("details") or []:
            if not x.get("scoringPlay"):
                continue
            who = [a.get("displayName") for a in x.get("athletesInvolved") or [] if a.get("displayName")]
            goals.append({"team_id": str((x.get("team") or {}).get("id") or ""), "player": who[0] if who else "",
                          "clock": (x.get("clock") or {}).get("displayValue") or "",
                          "kind": (x.get("type") or {}).get("text") or "Goal"})
        if "home" in sides and "away" in sides:
            matches.append({
                "id": str(ev.get("id") or ""),
                "date": ev.get("date"), "state": st.get("state"), "detail": st.get("shortDetail") or "",
                "status": st.get("name") or "",
                "home": sides["home"], "away": sides["away"], "goals": goals,
            })
    return {"matches": matches, "logo": league_logo(d)}


def zones(league):
    """Table colouring: ranks <= top are blue, <= mid are dim blue, the last `bottom` are red."""
    if league.startswith("uefa."):
        return {"top": 8, "mid": 24, "bottom": 0}  # league phase: last 16 / play-offs / out
    return {"top": 4, "mid": 0, "bottom": 3}


def one_league(lg):
    item = {"id": lg, "name": lg, "table": [], "matches": [], "zones": zones(lg)}
    try:
        item.update(CACHE.get(f"standings:{lg}", 600, lambda: fetch_standings(lg)))
    except Exception:
        pass
    item["name"] = LEAGUE_NAMES.get(lg, item["name"])
    try:
        # Refresh scores every minute while a match is on, otherwise every 5.
        prev = (CACHE._data.get(f"scores:{lg}") or (0, {}))[1]
        live = any(m["state"] == "in" for m in prev.get("matches", []))
        item.update(CACHE.get(f"scores:{lg}", 30 if live else 300, lambda: fetch_scores(lg)))
    except Exception:
        pass
    return item


POOL = ThreadPoolExecutor(max_workers=6)


def football(leagues):
    return {"leagues": list(POOL.map(one_league, leagues))}


TENNIS_ROUNDS = {"Final": "F", "Semifinal": "SF", "Quarterfinal": "QF", "Round of 16": "R16",
                 "Qualifying Final": "Q", "Qualifying 1st Round": "Q1", "Qualifying 2nd Round": "Q2", "Group Stage": "Grp"}


def parse_time(s):
    try:
        return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M%z").timestamp()
    except (AttributeError, ValueError):
        return None


def country(a):
    f = a.get("flag")
    return (f.get("alt") if isinstance(f, dict) else None) or a.get("flagAlt") or ""


def fetch_tennis(tour):
    """Singles matches from the last ~36 h and next ~24 h, grouped by tournament."""
    d = get_json(f"{ESPN}/site/v2/sports/tennis/{tour}/scoreboard")
    now = time.time()
    out = []
    for ev in d.get("events") or []:
        matches = []
        for g in ev.get("groupings") or []:
            if "Singles" not in ((g.get("grouping") or {}).get("displayName") or ""):
                continue
            for c in g.get("competitions") or []:
                st = (c.get("status") or {}).get("type") or {}
                ts = parse_time(c.get("date"))
                state = st.get("state")
                if state != "in" and (ts is None or ts < now - 36 * 3600 or ts > now + 24 * 3600):
                    continue
                players = []
                for x in c.get("competitors") or []:
                    a = x.get("athlete") or {}
                    players.append({
                        "id": str(a.get("id") or ""),
                        "name": a.get("shortName") or a.get("displayName") or "TBD",
                        "country": country(a),
                        "rank": (x.get("curatedRank") or {}).get("current"),
                        "sets": [int(l.get("value") or 0) for l in x.get("linescores") or []],
                        "winner": bool(x.get("winner")),
                    })
                if len(players) != 2 or any(p["name"] == "TBD" for p in players):
                    continue
                rnd = (c.get("round") or {}).get("displayName") or ""
                rnd = TENNIS_ROUNDS.get(rnd) or re.sub(r"^Round (\d+)$", r"R\1", rnd)
                matches.append({"id": str(c.get("id") or ""), "date": c.get("date"), "state": state, "detail": st.get("shortDetail") or "",
                                "round": rnd, "players": players})
        if matches:
            out.append({"name": ev.get("name") or "", "matches": matches})
    return out


def fetch_rankings(tour):
    d = get_json(f"{ESPN}/site/v2/sports/tennis/{tour}/rankings")
    rk = (d.get("rankings") or [{}])[0]
    ranks = []
    for r in (rk.get("ranks") or [])[:20]:
        a = r.get("athlete") or {}
        ranks.append({"rank": r.get("current"), "previous": r.get("previous"), "id": str(a.get("id") or ""), "name": a.get("displayName") or "",
                      "country": country(a), "points": r.get("points")})
    return {"updated": rk.get("update"), "ranks": ranks}


def one_tour(tour):
    item = {"id": tour, "name": tour.upper(), "tournaments": [], "rankings": []}
    try:
        live = any(m["state"] == "in" for t in (CACHE._data.get(f"tennis:{tour}") or (0, []))[1] for m in t["matches"])
        item["tournaments"] = CACHE.get(f"tennis:{tour}", 50 if live else 300, lambda: fetch_tennis(tour))
    except Exception:
        pass
    try:
        item["rankings"] = CACHE.get(f"rankings:{tour}", 6 * 3600, lambda: fetch_rankings(tour))["ranks"]
    except Exception:
        pass
    return item


def tennis(tours):
    return {"tours": list(POOL.map(one_tour, tours))}



# ---------------------------------------------------------------- AI: Hugging Face trending models and daily papers

HF = "https://huggingface.co/api"
# Trending lists pick up joke and uncensored fine-tunes; these words keep the list on real releases.
HF_SKIP = re.compile(r"uncensored|abliterat|heretic|nsfw|erotic|porn|jailbreak", re.I)


def fetch_hf_models():
    out = []
    for m in get_json(f"{HF}/models?sort=trendingScore&limit=40", timeout=15):
        mid = m.get("id") or m.get("modelId") or ""
        if not mid or HF_SKIP.search(mid) or any(HF_SKIP.search(t) for t in m.get("tags") or []):
            continue
        author, _, name = mid.partition("/")
        out.append({"id": mid, "author": author if name else "", "name": name or mid, "likes": m.get("likes") or 0,
                    "downloads": m.get("downloads") or 0, "task": (m.get("pipeline_tag") or "").replace("-", " "),
                    "created": m.get("createdAt")})
        if len(out) >= 15:
            break
    return out


def fetch_hf_papers():
    out = []
    for d in get_json(f"{HF}/daily_papers?limit=30", timeout=15):
        p = d.get("paper") or {}
        title = clean(p.get("title") or d.get("title"), 200)
        if not title:
            continue
        summary = clean(p.get("ai_summary") or p.get("summary") or d.get("summary"), 400)
        # The abstract's first sentence or two is enough for a glance.
        m = re.match(r"(.{60,240}?[.!?])(\s|$)", summary or "")
        org = (p.get("organization") or d.get("organization") or {})
        out.append({"id": p.get("id") or "", "title": title, "summary": m.group(1) if m else (summary or "")[:220],
                    "upvotes": p.get("upvotes") or 0, "org": (org.get("fullname") or org.get("name") or "") if isinstance(org, dict) else "",
                    "published": d.get("publishedAt") or p.get("publishedAt")})
    out.sort(key=lambda x: -x["upvotes"])
    return out[:15]


def ai_feed():
    out = {"models": [], "papers": []}
    for key, ttl, fn in (("models", 1800, fetch_hf_models), ("papers", 3600, fetch_hf_papers)):
        try:
            out[key] = CACHE.get(f"hf:{key}", ttl, fn)
        except Exception as e:
            log(f"hugging face {key}: {e}")
    return out


# ---------------------------------------------------------------- basketball (EuroLeague / EuroCup)

EL_API = "https://api-live.euroleague.net/v2"
BB_NAMES = {"E": "EuroLeague", "U": "EuroCup"}


def bb_season():
    now = datetime.utcnow()
    return now.year if now.month >= 8 else now.year - 1


def bb_club(side):
    c = (side or {}).get("club") or {}
    return {"id": c.get("code") or "", "team": c.get("abbreviatedName") or c.get("name") or "",
            "abbr": c.get("tvCode") or c.get("code") or "", "logo": ((c.get("images") or {}).get("crest") or "")}


def bb_live(comp, season, code):
    """Live score, quarter and clock for one game (EuroLeague's live feed)."""
    h = get_json(f"https://live.euroleague.net/api/Header?gamecode={code}&seasoncode={comp}{season}")
    q = str(h.get("Quarter") or "").strip()
    rem = str(h.get("RemainingPartialTime") or "").strip()
    return {"live": bool(h.get("Live")), "a": str(h.get("ScoreA") or "0").strip(), "b": str(h.get("ScoreB") or "0").strip(),
            "q": int(q) if q.isdigit() else 0, "rem": rem, "time": str(h.get("GameTime") or "")}


def bb_detail(q, rem):
    if not q:
        return "Live"
    name = f"Q{q}" if q <= 4 else ("OT" if q == 5 else f"OT{q - 4}")
    if rem in ("00:00", "0:00"):
        return "Half time" if q == 2 else f"End of {name}"
    return f"{name} {rem}"


def fetch_bb_games(comp, season):
    """Games of the current round (the one with the next unplayed game), plus last round's results."""
    data = get_json(f"{EL_API}/competitions/{comp}/seasons/{comp}{season}/games", timeout=20).get("data") or []
    now = time.time()
    games = []
    for g in data:
        t = parse_iso(g.get("utcDate") or "")
        if t is None:
            continue
        played = bool(g.get("played"))
        # The API has no "live" flag: a started game counts as live until it's marked played (max 3 h).
        state = "post" if played else ("in" if t <= now < t + 3 * 3600 else "pre")
        home, away = bb_club(g.get("local")), bb_club(g.get("road"))
        if state != "pre":
            home["score"] = str((g.get("local") or {}).get("score") or 0)
            away["score"] = str((g.get("road") or {}).get("score") or 0)
        if state == "in":
            # Confirm with the live feed: real quarter, clock and score, or "not started" / "finished".
            try:
                lv = CACHE.get(f"bblive:{comp}:{g.get('gameCode')}", 25, lambda: bb_live(comp, season, g.get("gameCode")))
                if lv["live"]:
                    home["score"], away["score"] = lv["a"], lv["b"]
                    g["_detail"] = bb_detail(lv["q"], lv["rem"])
                    g["_q"] = lv["q"]
                elif lv["time"].startswith("40:") or lv["q"] > 4 or (lv["a"] not in ("", "0") and not lv["q"]):
                    state = "post"
                    home["score"], away["score"] = lv["a"], lv["b"]
                else:
                    state = "pre"
                    home.pop("score", None); away.pop("score", None)
            except Exception as e:
                log(f"basketball live {comp} {g.get('gameCode')}: {e}")
        rnd = g.get("round")
        games.append({"id": g.get("identifier"), "quarter": g.get("_q") or 0, "date": g.get("utcDate"), "state": state, "round": rnd,
                      "round_name": g.get("roundName") or f"Round {rnd}", "detail": (g.get("_detail") or "Live") if state == "in" else "",
                      "group": ((g.get("group") or {}).get("name") or ""), "home": home, "away": away, "t": t})
    games.sort(key=lambda g: g["t"])
    nxt = next((g for g in games if g["state"] != "post"), None)
    last_played = max((g["round"] for g in games if g["state"] == "post"), default=None)
    cur = nxt["round"] if nxt else last_played
    rnd = [g for g in games if g["round"] == cur]
    prev = [g for g in games if g["round"] == last_played and last_played != cur]
    for g in games:
        g.pop("t", None)
    return {"matches": rnd, "previous": prev, "last_played": last_played,
            "round_name": rnd[0]["round_name"] if rnd else ""}


def fetch_bb_standings(comp, season, rnd):
    d = get_json(f"{EL_API}/competitions/{comp}/seasons/{comp}{season}/rounds/{rnd}/standings", timeout=20)
    groups = []
    for grp in d if isinstance(d, list) else []:
        rows = []
        for r in grp.get("standings") or []:
            c, x = r.get("club") or {}, r.get("data") or {}
            rows.append({"rank": x.get("position"), "id": c.get("code") or "",
                         "team": c.get("abbreviatedName") or c.get("name") or "",
                         "logo": (c.get("images") or {}).get("crest") or "",
                         "p": x.get("gamesPlayed"), "w": x.get("gamesWon"), "l": x.get("gamesLost"),
                         "diff": (x.get("pointsFavour") or 0) - (x.get("pointsAgainst") or 0)})
        rows.sort(key=lambda r: r["rank"] or 99)
        groups.append({"name": (grp.get("group") or {}).get("name") or "", "table": rows})
    return sorted(groups, key=lambda g: g["name"])


def one_bb(comp):
    season = bb_season()
    item = {"id": comp, "name": BB_NAMES.get(comp, comp), "matches": [], "previous": [], "groups": []}
    try:
        live = any(m["state"] == "in" for m in ((CACHE._data.get(f"bb:{comp}") or (0, {}))[1] or {}).get("matches", []))
        item.update(CACHE.get(f"bb:{comp}", 45 if live else 300, lambda: fetch_bb_games(comp, season)))
    except Exception as e:
        log(f"basketball {comp}: {e}")
    if item.get("last_played"):
        try:
            rnd = item["last_played"]
            item["groups"] = CACHE.get(f"bbst:{comp}:{rnd}", 600, lambda: fetch_bb_standings(comp, season, rnd))
        except Exception as e:
            log(f"basketball standings {comp}: {e}")
    # Playoff zones: EuroLeague top 6 straight in, 7-10 play-in.
    item["zones"] = {"top": 6, "mid": 10} if comp == "E" else {"top": 0, "mid": 0}
    return item


def basketball(comps):
    return {"leagues": list(POOL.map(one_bb, [c for c in comps if c in BB_NAMES]))}


# ---------------------------------------------------------------- news (RSS / Atom)

SOURCE_NAMES = {"bbc": "BBC", "bbci": "BBC", "dw": "DW", "ntv": "NTV", "hurriyet": "Hürriyet", "aa": "AA",
                "trthaber": "TRT Haber", "sozcu": "Sözcü", "theguardian": "Guardian", "reuters": "Reuters",
                "huggingface": "Hugging Face", "openai": "OpenAI", "deepmind": "DeepMind", "google": "Google",
                "theverge": "The Verge", "technologyreview": "MIT Tech Review", "simonwillison": "Simon Willison",
                "techcrunch": "TechCrunch", "arstechnica": "Ars Technica"}


def source_name(url):
    host = urllib.parse.urlparse(url).hostname or ""
    parts = [p for p in host.split(".") if p not in ("www", "feeds", "rss", "com", "co", "uk", "tr", "org", "net")]
    for p in parts:
        if p in SOURCE_NAMES:
            return SOURCE_NAMES[p]
    return (parts[0] if parts else host).upper()


def clean(text, limit):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return snippet(html.unescape(text), limit)


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fetch_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with OPENER.open(req, timeout=10) as r:
        data = r.read(3_000_000)
    if data[:2] == b"\x1f\x8b":  # some feeds (DeepMind) send gzip even when not asked
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    src = source_name(url)
    out = []
    for e in root.iter():
        if e.tag.split("}")[-1] not in ("item", "entry"):
            continue
        f = {}
        for c in e:
            name = c.tag.split("}")[-1]
            if name == "link" and c.get("href"):
                f.setdefault("link", c.get("href"))
            elif c.text:
                f.setdefault(name, c.text)
        title = clean(f.get("title"), 160)
        if not title:
            continue
        out.append({
            "title": title,
            "summary": clean(f.get("description") or f.get("summary") or f.get("content"), 220),
            "link": (f.get("link") or "").strip(),
            "source": src,
            "time": parse_date(f.get("pubDate") or f.get("date") or f.get("published") or f.get("updated")),
        })
    return out


def one_news_group(group):
    items = []
    for url in group.get("feeds") or []:
        try:
            items += CACHE.get(f"feed:{url}", 600, lambda: fetch_feed(url))
        except Exception:
            pass
    words = [w.lower() for w in group.get("exclude") or [] if isinstance(w, str) and w]
    if words:
        items = [i for i in items if not any(w in i["title"].lower() for w in words)]
    items.sort(key=lambda i: i["time"] or 0, reverse=True)
    seen, unique = set(), []
    for i in items:
        key = re.sub(r"\W+", "", i["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            unique.append(i)
    # Take turns between sources (newest first within each), so a busy outlet can't crowd out the rest.
    by_src = {}
    for i in unique:
        by_src.setdefault(i["source"], []).append(i)
    queues = sorted(by_src.values(), key=lambda q: q[0]["time"] or 0, reverse=True)
    mixed = []
    while len(mixed) < 15 and any(queues):
        for q in queues:
            if q and len(mixed) < 15:
                mixed.append(q.pop(0))
    return {"name": group.get("name") or "News", "items": mixed}


def news(groups):
    groups = [g for g in groups or [] if isinstance(g, dict)]
    return {"groups": list(POOL.map(one_news_group, groups))}


# ---------------------------------------------------------------- followed teams

PREFS_LOCK = threading.Lock()


def load_prefs():
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            p = json.load(f)
        return p if isinstance(p, dict) else {}
    except (OSError, ValueError):
        return {}


# Settings the dashboard's settings panel may change (stored in prefs.json, layered over config.json).
# Secrets (stock key, calendar links) and machine settings (port, display) are never editable from the page.
SETTING_CHOICES = {
    "football": ["tur.1", "eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions", "uefa.europa", "uefa.europa.conf"],
    "tennis": ["atp", "wta"],
    "basketball": ["E", "U"],
    "markets": ["USD", "EUR", "GBP", "GRA", "CEYREKALTIN", "CHF", "JPY"],
    "crypto": ["bitcoin", "ethereum", "solana", "ripple", "dogecoin", "cardano"],
}
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def clean_settings(d):
    out = {}
    if not isinstance(d, dict):
        return out
    loc = d.get("location")
    if isinstance(loc, dict):
        try:
            lat, lon = float(loc.get("lat")), float(loc.get("lon"))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                out["location"] = {"name": snippet(str(loc.get("name") or ""), 60), "lat": round(lat, 4), "lon": round(lon, 4)}
        except (TypeError, ValueError):
            pass
    for key, lo, hi in (("rotate_seconds", 6, 60), ("kickoff_reminder_minutes", 0, 120)):
        if isinstance(d.get(key), (int, float)) and not isinstance(d.get(key), bool):
            out[key] = int(min(hi, max(lo, d[key])))
    if d.get("goal_alerts") in ("followed", "all"):
        out["goal_alerts"] = d["goal_alerts"]
    if d.get("night_clock") in ("off", "auto"):
        out["night_clock"] = d["night_clock"]
    nd = d.get("night_dim")
    if isinstance(nd, dict) and HHMM.fullmatch(str(nd.get("start") or "")) and HHMM.fullmatch(str(nd.get("end") or "")):
        try:
            out["night_dim"] = {"start": nd["start"], "end": nd["end"], "opacity": round(min(1.0, max(0.1, float(nd.get("opacity")))), 2)}
        except (TypeError, ValueError):
            pass
    for key, allowed in SETTING_CHOICES.items():
        if isinstance(d.get(key), list):
            out[key] = [x for x in allowed if x in d[key]]  # keeps the canonical order
    return out


def effective_config(base):
    """config.json with the settings panel's choices on top."""
    cfg = dict(base)
    cfg.update(clean_settings(load_prefs().get("settings") or {}))
    return cfg


def save_prefs(data):
    """Updates the parts of prefs.json that `data` contains: followed teams, players, settings."""
    with PREFS_LOCK:
        prefs = load_prefs()
        if "follow" in data:
            follow = []
            for t in (data.get("follow") or [])[:40]:
                if isinstance(t, dict) and re.fullmatch(r"\d{1,9}", str(t.get("id") or "")):
                    follow.append({"id": str(t["id"]), "name": snippet(str(t.get("name") or ""), 60)})
            prefs["follow"] = follow
        if "players" in data:
            players = []
            for t in (data.get("players") or [])[:30]:
                if isinstance(t, dict) and re.fullmatch(r"\d{1,12}", str(t.get("id") or "")):
                    players.append({"id": str(t["id"]), "name": snippet(str(t.get("name") or ""), 60),
                                    "tour": "wta" if t.get("tour") == "wta" else "atp"})
            prefs["players"] = players
        if "settings" in data:
            prefs["settings"] = clean_settings(data.get("settings"))
        tmp = PREFS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, PREFS_PATH)
    return public_prefs()


def public_prefs():
    p = load_prefs()
    return {"follow": p.get("follow") or [], "players": p.get("players") or [], "settings": clean_settings(p.get("settings") or {})}


def fetch_fixtures(league, team_id):
    d = get_json(f"{ESPN}/site/v2/sports/soccer/{league}/teams/{team_id}/schedule?fixture=true")
    out = []
    for ev in d.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        sides = {}
        for c in comp.get("competitors") or []:
            t = c.get("team") or {}
            sides[c.get("homeAway")] = {"id": str(t.get("id") or ""),
                                        "team": t.get("shortDisplayName") or t.get("displayName") or ""}
        if "home" in sides and "away" in sides and ev.get("date"):
            out.append({"id": str(ev.get("id") or ""), "date": ev["date"], "home": sides["home"], "away": sides["away"]})
    return out


def my_teams(cfg):
    follow = load_prefs().get("follow") or []
    if not follow:
        return {"teams": []}
    leagues = football(cfg.get("football") or [])["leagues"]
    now = time.time()
    teams = []
    for t in follow:
        tid = t["id"]
        item = {"id": tid, "name": t.get("name") or "", "logo": None, "standings": [], "next": None}
        for lg in leagues:
            row = next((r for r in lg["table"] if r.get("id") == tid), None)
            if not row:
                continue
            item["name"] = row.get("full") or row["team"] or item["name"]
            item["logo"] = item["logo"] or row.get("logo")
            item["standings"].append({"league": lg["name"], "league_id": lg["id"], "logo": lg.get("logo"),
                                      "rank": row["rank"], "pts": row["pts"], "played": row["played"]})
            try:
                fx = CACHE.get(f"fixtures:{lg['id']}:{tid}", 3600, lambda: fetch_fixtures(lg["id"], tid))
            except Exception:
                fx = []
            for m in fx:
                ts = parse_time(m["date"])
                if ts and ts > now - 3 * 3600 and (item["next"] is None or ts < item["next"]["ts"]):
                    item["next"] = dict(m, ts=ts, league=lg["name"], logo=lg.get("logo"))
        teams.append(item)
    return {"teams": teams}


# ---------------------------------------------------------------- crypto + stocks

CRYPTO_NAMES = {"bitcoin": ("BTC", "Bitcoin"), "ethereum": ("ETH", "Ethereum"), "solana": ("SOL", "Solana"),
                "ripple": ("XRP", "XRP"), "binancecoin": ("BNB", "BNB"), "dogecoin": ("DOGE", "Dogecoin"),
                "cardano": ("ADA", "Cardano"), "tether": ("USDT", "Tether")}


def fetch_crypto(ids):
    ids = [i for i in ids if re.fullmatch(r"[a-z0-9-]{1,40}", i)]
    if not ids:
        return []
    d = get_json("https://api.coingecko.com/api/v3/simple/price?ids=" + ",".join(ids) +
                 "&vs_currencies=try,usd&include_24hr_change=true")
    out = []
    for i in ids:
        row = d.get(i)
        if not isinstance(row, dict):
            continue
        code, name = CRYPTO_NAMES.get(i, (i[:5].upper(), i.replace("-", " ").title()))
        out.append({"code": code, "name": name, "kind": "crypto", "sell": num(row.get("try")),
                    "usd": num(row.get("usd")), "change": num(row.get("try_24h_change"))})
    return out


def fetch_stocks(key, symbols):
    q = urllib.parse.urlencode({"symbol": ",".join(symbols), "apikey": key})
    d = get_json(f"https://api.twelvedata.com/quote?{q}")
    if len(symbols) == 1:
        d = {symbols[0]: d}
    out = []
    for sym in symbols:
        row = d.get(sym) or {}
        if not isinstance(row, dict) or row.get("status") == "error" or row.get("close") is None:
            if isinstance(row, dict) and row.get("message"):
                log(f"stocks: {sym}: {row.get('message')}")
            continue
        out.append({"code": sym, "name": row.get("name") or sym, "kind": "stock", "sell": num(row.get("close")),
                    "currency": row.get("currency") or "", "change": num(row.get("percent_change")),
                    "open": bool(row.get("is_market_open"))})
    return out


FX_CODES = {"USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "CNY", "PLN", "HUF", "CZK"}
GRAMS_PER_OZ = 31.1034768
GOLD_CODES = {"GRA": 1.0, "CEYREKALTIN": 1.75, "YARIMALTIN": 3.5, "TAMALTIN": 7.0}  # approx. grams of gold


def fetch_fx_history(codes, days=30):
    """Daily TL prices from the ECB (via Frankfurter): {code: [[date, tl], ...]}."""
    codes = [c for c in codes if c in FX_CODES]
    if not codes:
        return {}
    start = (datetime.utcnow() - timedelta(days=days + 4)).strftime("%Y-%m-%d")
    d = get_json(f"https://api.frankfurter.dev/v1/{start}..?base=TRY&symbols={','.join(codes)}")
    out = {c: [] for c in codes}
    for day in sorted(d.get("rates") or {}):
        for c, v in d["rates"][day].items():
            if c in out and v:
                out[c].append([day, round(1 / v, 4)])
    return out


def fetch_coin_history(coin, days=30):
    d = get_json(f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=try&days={days}&interval=daily")
    return [[datetime.utcfromtimestamp(t / 1000).strftime("%Y-%m-%d"), v] for t, v in d.get("prices") or []]


def history(cfg):
    """30-day trend lines. Gold uses PAX Gold (one token = one troy ounce) as a stand-in."""
    out = {}
    codes = cfg.get("markets") or []
    try:
        out.update(CACHE.get("hist:fx", 6 * 3600, lambda: fetch_fx_history(codes)))
    except Exception:
        pass
    gold = [c for c in codes if c in GOLD_CODES]
    if gold:
        try:
            oz = CACHE.get("hist:pax-gold", 3 * 3600, lambda: fetch_coin_history("pax-gold"))
            for c in gold:
                out[c] = [[d, v / GRAMS_PER_OZ * GOLD_CODES[c]] for d, v in oz]
        except Exception:
            pass
    for coin in cfg.get("crypto") or []:
        code = CRYPTO_NAMES.get(coin, (coin[:5].upper(),))[0]
        try:
            out[code] = CACHE.get(f"hist:{coin}", 3 * 3600, lambda: fetch_coin_history(coin))
        except Exception:
            pass
    return out


def markets(cfg):
    out = {"items": [], "crypto": [], "stocks": []}
    try:
        out.update(CACHE.get("markets", 60, lambda: fetch_markets(cfg.get("markets") or [])))
    except Exception:
        pass
    try:
        out["crypto"] = CACHE.get("crypto", 120, lambda: fetch_crypto(cfg.get("crypto") or []))
    except Exception:
        pass
    st = cfg.get("stocks") or {}
    key, symbols = (st.get("api_key") or "").strip(), [x for x in st.get("symbols") or [] if isinstance(x, str)][:8]
    if key and symbols:
        # Free plan: ~800 credits a day, one per symbol per request.
        ttl = max(300, math.ceil(len(symbols) * 86400 / 750))
        try:
            out["stocks"] = CACHE.get("stocks", ttl, lambda: fetch_stocks(key, symbols))
        except Exception:
            pass
    out["history"] = history(cfg)
    return out


# ---------------------------------------------------------------- Apple Reminders

class Reminders:
    """Reads the Reminders app in the background (it takes several seconds)."""

    def __init__(self):
        self.data = {"enabled": False, "items": []}
        self._started = False

    def start(self, cfg):
        rc = cfg.get("reminders") or {}
        if not rc.get("enabled") or self._started:
            return
        self._started = True
        self.data = {"enabled": True, "items": [], "loading": True}
        threading.Thread(target=self._loop, args=(rc,), daemon=True).start()

    def _loop(self, rc):
        script = str(ROOT / "scripts" / "reminders.js")
        lists = json.dumps([x for x in rc.get("lists") or [] if isinstance(x, str)])
        days = int(rc.get("days_ahead") or 7)
        while True:
            try:
                r = subprocess.run(["osascript", "-l", "JavaScript", script, lists],
                                   capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or "osascript failed").strip()[:200])
                items = json.loads(r.stdout).get("items") or []
                horizon = time.time() + days * 86400
                keep = [i for i in items if i.get("due") and parse_iso(i["due"]) and parse_iso(i["due"]) <= horizon]
                keep.sort(key=lambda i: parse_iso(i["due"]))
                self.data = {"enabled": True, "items": keep, "undated": sum(1 for i in items if not i.get("due")),
                             "updated": time.time()}
            except Exception as e:
                log(f"reminders: {e}")
                self.data = dict(self.data, error=str(e)[:200], loading=False)
            time.sleep(180)


def parse_iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


REMINDERS = Reminders()


class Spotify:
    """Watches the Spotify app: every 2 s while it runs, otherwise checks every 5 s whether it started."""

    SCRIPT = str(ROOT / "scripts" / "spotify.js")
    COMMANDS = ("playpause", "next", "previous")

    def __init__(self):
        self.data = {"running": False}
        self._lock = threading.Lock()

    def start(self, cfg):
        if (cfg.get("spotify") or {}).get("enabled", True):
            threading.Thread(target=self._loop, daemon=True).start()

    def _running(self):
        return subprocess.run(["pgrep", "-x", "Spotify"], capture_output=True).returncode == 0

    def read(self, cmd=None):
        with self._lock:
            try:
                args = ["osascript", "-l", "JavaScript", self.SCRIPT] + ([cmd] if cmd else [])
                r = subprocess.run(args, capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or "osascript failed").strip()[:200])
                d = json.loads(r.stdout)
                d["at"] = time.time()
                self.data = d
            except Exception as e:
                log(f"spotify: {e}")
                self.data = {"running": False, "error": str(e)[:200]}
            return self.data

    def _loop(self):
        while True:
            if self._running():
                self.read()
                time.sleep(2)
            else:
                self.data = {"running": False}
                time.sleep(5)


SPOTIFY = Spotify()

# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "sidescreen/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # hooks fire constantly; keep the log quiet

    def _host_ok(self):
        # Only answer to local names, so other websites can't read status via DNS rebinding.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        if body or code != 204:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _same_origin(self):
        # Writes from the page must come from the dashboard itself, as JSON (which a
        # cross-site form can't send without a CORS preflight this server never approves).
        origin = (self.headers.get("Origin") or "").rstrip("/")
        port = self.server.server_address[1]
        ok_origin = origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
        return ok_origin and (self.headers.get("Content-Type") or "").startswith("application/json")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 2_000_000)) if length else b""
        path = self.path.split("?")[0]
        if path == "/api/prefs":
            if not (self._host_ok() and self._same_origin()):
                return self._send(403)
            try:
                return self._json(save_prefs(json.loads(raw or b"{}")))
            except (ValueError, AttributeError):
                return self._send(400)
        if path == "/api/spotify":
            if not (self._host_ok() and self._same_origin()):
                return self._send(403)
            try:
                cmd = json.loads(raw or b"{}").get("cmd")
            except (ValueError, AttributeError):
                return self._send(400)
            if cmd not in Spotify.COMMANDS:
                return self._send(400)
            return self._json(SPOTIFY.read(cmd))
        if path != "/api/hook":
            return self._send(404)
        # Always 204: "success, no decision" for Claude Code. Never block a session.
        if self._host_ok():
            try:
                SESSIONS.handle(json.loads(raw or b"{}"))
            except Exception as e:
                log(f"bad hook payload: {e}")
        self._send(204)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403)
        path = self.path.split("?")[0]
        cfg = effective_config(self.server.config)
        if path == "/api/status":
            return self._json({"sessions": SESSIONS.snapshot(), "now": time.time(), "version": version()})
        if path == "/api/config":
            return self._json({k: cfg[k] for k in PUBLIC_KEYS if k in cfg})
        if path == "/api/calendar":
            try:
                return self._json(CALENDAR.get(cfg))
            except Exception as e:
                log(f"calendar error: {e}")
                return self._json({"enabled": True, "events": [], "errors": 1})
        if path == "/api/markets":
            return self._json(markets(cfg))
        if path == "/api/prefs":
            return self._json(public_prefs())
        if path == "/api/settings":
            # Current values and the choices the settings panel offers.
            return self._json({"values": {k: cfg.get(k) for k in ("location", "rotate_seconds", "kickoff_reminder_minutes",
                                                                    "goal_alerts", "night_clock", "night_dim", *SETTING_CHOICES)},
                               "choices": SETTING_CHOICES, "names": {**LEAGUE_NAMES, "atp": "ATP", "wta": "WTA", **BB_NAMES}})
        if path == "/api/usage":
            return self._json(USAGE.get(cfg))
        if path == "/api/myteams":
            return self._json(my_teams(cfg))
        if path == "/api/spotify":
            return self._json(SPOTIFY.data)
        if path == "/api/reminders":
            return self._json(REMINDERS.data)
        if path == "/api/football":
            return self._json(football(cfg.get("football") or []))
        if path == "/api/news":
            return self._json(news(cfg.get("news")))
        if path == "/api/ai":
            return self._json(ai_feed() if cfg.get("ai", True) else {"models": [], "papers": []})
        if path == "/api/basketball":
            return self._json(basketball(cfg.get("basketball") or []))
        if path == "/api/tennis":
            return self._json(tennis(cfg.get("tennis") or []))
        if path == "/api/health":
            return self._json({"ok": True})
        return self._static(path)

    do_HEAD = do_GET

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self._send(404)
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}.get(
            target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)


class RemoteHandler(BaseHTTPRequestHandler):
    """The one thing open to the network: hook events from Claude Code on your other Macs.
    Needs the shared token (config "remote.token"); answers nothing else, and never serves the dashboard."""
    server_version = "sidescreen/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self):
        token = self.server.token
        sent = self.headers.get("X-Sidescreen-Token") or ""
        return bool(token) and hmac.compare_digest(sent.encode(), token.encode())

    def do_GET(self):
        # For checking the connection from the other Mac: 204 = token accepted.
        if self.path.split("?")[0] == "/api/ping":
            return self._send(204 if self._authorized() else 401)
        self._send(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if self.path.split("?")[0] != "/api/hook" or length > 2_000_000:
            return self._send(404)
        raw = self.rfile.read(length) if length else b""
        if not self._authorized():
            return self._send(401)
        machine = re.sub(r"[^\w .'-]", "", self.headers.get("X-Sidescreen-Machine") or "")[:24].strip() or "Remote"
        try:
            SESSIONS.handle(json.loads(raw or b"{}"), machine=machine)
        except Exception as e:
            log(f"bad remote hook payload: {e}")
        self._send(204)  # like the local hook: never hold up a session


def start_remote(cfg):
    rc = cfg.get("remote") or {}
    token = str(rc.get("token") or "")
    if not rc.get("enabled"):
        return
    if len(token) < 24:
        log("remote hooks: not started, remote.token must be at least 24 characters (run scripts/remote.py enable)")
        return
    port = int(rc.get("port") or 8766)
    srv = ThreadingHTTPServer(("0.0.0.0", port), RemoteHandler)
    srv.daemon_threads = True
    srv.token = token
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"remote hooks: listening on port {port} (token required)")


def main():
    cfg = load_config()
    port = int(os.environ.get("SIDESCREEN_PORT") or cfg.get("port") or 8765)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    httpd.config = cfg
    REMINDERS.start(cfg)
    SPOTIFY.start(cfg)
    APPLE_CAL.start(cfg)
    start_remote(cfg)
    log(f"sidescreen listening on http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
