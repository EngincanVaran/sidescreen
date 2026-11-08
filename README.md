# Sidescreen

An always-on information dashboard for the Mac mini's portable display (1920×1080).

**Ambient mode**
- Clock, date, and a **weather card** with a 7-day forecast that takes turns with your Apple Reminders and agenda, a **Spotify** pill (cover, track, artist, progress, previous / play-pause / next) that appears while the Spotify app plays and folds away 2 minutes after you pause, and a small **YouTube** button that expands into a link box (or just paste a YouTube link anywhere on the dashboard).
- **Info card** (rotates): TL rates and gram gold with 30-day trend lines, crypto (and stocks, with a key), World news and Türkiye news.
- **Sports card** (rotates, with Football / Basketball / Tennis tabs): your teams, today's football with live scores, league tables (Süper Lig, Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Champions League, Europa League, Conference League), EuroLeague and EuroCup games and standings (clubs you follow in football are highlighted in basketball too), ATP/WTA matches and rankings. Competition logos, and a red LIVE badge when something is being played.
- **Follow teams and tennis players** with the star button on the sports card (ATP / WTA players tabs for tennis). Followed teams are highlighted in tables and matches, shown first, and get a "Your teams" panel with their position and next fixture. Followed players are highlighted in matches and rankings, and once you follow any, tennis alerts are for them only.
- **Alerts** at the top of the screen (also over a video). Click one to dismiss it. At most 4 show at once; goals stay longest.
  - Football: a reminder before your teams play, kick-off, every goal with the scorer (own goals credited to the right team; if ESPN hasn't named the scorer yet, the alert fills it in when it does), goals ruled out by VAR, half time and full time.
  - Basketball (EuroLeague / EuroCup): tip-off, the end of each quarter and half time with who's leading, and the final score.
  - Tennis: match start, each set, and the winner, for matches with a top-10 player or from the quarter-finals on.
  - Reminders when they're due, and Claude sessions that need you.
- Claude Code sessions: needs you, working, done. "Needs you" covers permission prompts, Claude's question dialogs and plan approvals, and replies that end by asking you something. The card grows and shrinks with the number of sessions, and the sports card uses the space it frees. Session states are kept in `sessions.json`, so they survive a server restart. A line under the title shows today's usage: tokens (including cache), output tokens, active time, the busiest projects and tokens per hour. It's read from Claude Code's own transcripts in `~/.claude/projects`, nothing leaves the Mac.
- **Night clock** (moon button next to YouTube): a large, dim clock with the date, temperature, your next event or match, live scores of your teams and Claude sessions that need you. Tap to wake. Set it to come on by itself during the night hours in Settings.
- **Settings** (gear button): weather location (city search), rotation speed, alerts, night hours, brightness and night clock, and which leagues, competitions, tours, rates and crypto to show. Saved to `prefs.json` on top of `config.json`; the stock key, calendar links, port and display stay in `config.json` only.
- **Pick a view** on the sports card: under the Football / Basketball / Tennis tabs, a row of chips lists every view of that sport (Your teams, Today, each league table, EuroLeague / EuroCup games and tables, ATP / WTA matches and rankings). Tap one to keep it on screen; it gets a blue dot and stops rotating, even after a reload. Tap it again (or a sport tab, or the card) to resume rotating. A red dot on a chip means something in it is live.
- Click a rotating card to skip to its next panel. The page reloads itself when the server or page is updated.

**Match mode**: paste a YouTube link and the video takes ~75% of the screen, with a slim rail on the right: clock, weather, rates, live scores (your teams' games today first, then every live match, as many as fit), Claude sessions, and Next (the sooner of your next calendar event and your teams' next match). Goal and kick-off alerts, reminders, and an amber alert when a Claude session needs you appear over the video. Esc exits.

Dims at night and drifts a few pixels every 2 minutes to avoid burn-in.

## Install

Needs macOS, Python 3.9+ (the one that comes with the Xcode command line tools is fine) and Google Chrome. Claude Code is optional: without it the sessions card just stays empty.

```bash
git clone https://github.com/EngincanVaran/sidescreen.git
cd sidescreen
./install.sh
```

This will:
1. Create `config.json` from `config.example.json` if it doesn't exist yet. Your `config.json`, `prefs.json` (followed teams) and `sessions.json` stay out of git.
2. Add two LaunchAgents: `com.sidescreen.server` (the server, restarted if it crashes) and `com.sidescreen.display` (opens Chrome fullscreen on the portable screen at login).
3. Add HTTP hooks to `~/.claude/settings.json`, after backing it up to `settings.json.bak-sidescreen-<time>`. Your other settings and hooks stay as they are. Re-running doesn't add duplicates.

Options: `--with-calendar` also installs the calendar libraries, and `--no-hooks` leaves `settings.json` alone.

Remove everything with `./uninstall.sh` (add `--purge` to also delete the dashboard's Chrome profile at `~/.sidescreen-chrome`).

Logs are in `~/Library/Logs/sidescreen-server.log` and `sidescreen-display.log`.

## Try it without installing

```bash
python3 server.py
```

(Copy `config.example.json` to `config.json` first if you want to change settings.) Then open http://127.0.0.1:8765. Add `?demo` to the address to see sample sessions, agenda, rates, news and sports.

Send a fake hook event:

```bash
curl -X POST http://127.0.0.1:8765/api/hook -d '{"hook_event_name":"PermissionRequest","session_id":"t1","cwd":"/tmp/demo","tool_name":"Bash","tool_input":{"command":"ls"}}'
```

## Calendar

The agenda reads the Mac's own calendars directly: everything Calendar.app shows (iCloud, Google, Exchange accounts added in System Settings), with repeating events, and events that span several days on each day they cover.

macOS only lets apps (not scripts) ask for calendar access, so `install.sh` builds a tiny helper app, `build/Sidescreen Calendar.app`, from `scripts/calendar-helper/` (needs the Xcode command line tools: `xcode-select --install`). The server runs it every 5 minutes. The first time, macOS asks whether "Sidescreen Calendar" may access your calendars; click Allow. If you said no, allow it in System Settings → Privacy & Security → Calendars. Rebuilding the helper may ask again. Limit it to some calendars with `apple_calendar.calendars`, or turn it off with `"apple_calendar": {"enabled": false}`.

You can also add iCal links (a calendar's address in the standard `.ics` format), for calendars that aren't on this Mac:

- **Apple Calendar (iCloud)**: in Calendar, right-click the calendar → Share Calendar → tick **Public Calendar** and copy the `webcal://` link. Anyone who has that link can read the calendar, so keep it private.
- **Google Calendar**: Settings → your calendar → "Secret address in iCal format".

Put the links in `calendars` in `config.json` and run `./install.sh --with-calendar`. The links are never sent to the page.

## Apple Reminders

Open reminders due in the next 30 days (and overdue ones) show in the info card, and an alert pops up when one is due (all-day ones at 09:00). The first time the server reads the Reminders app, macOS asks whether "python3" may control Reminders. If you said no, allow it in System Settings → Privacy & Security → Automation.

## Spotify

The server reads the Spotify desktop app directly (every 2 seconds while it's open), so there's no Spotify login or API key. It never opens Spotify itself. The first time, macOS asks whether "python3" may control Spotify; if you said no, allow it in System Settings → Privacy & Security → Automation. To turn it off, set `"spotify": {"enabled": false}` in `config.json`.

## Stocks

Stocks need a free key from [Twelve Data](https://twelvedata.com) (about 800 requests a day). Put it in `stocks.api_key` and the symbols you want in `stocks.symbols`. The server spaces out requests to stay inside the free limit. Check the free plan covers the exchange you want (e.g. Borsa Istanbul) before relying on it.

## Config (`config.json`)

| Key | What it does |
|---|---|
| `port` | Server port (default 8765). Re-run `install.sh` after changing it. |
| `display` | Part of the screen name to open on, e.g. `"ASUS"`. Empty = the smallest non-main screen. Re-run `install.sh` after changing it. |
| `location`, `timezone`, `locale`, `hour12` | Weather location and clock format. |
| `apple_calendar` | `enabled` (default `true`) and `calendars` (names; empty = all). |
| `calendars`, `calendar_days_ahead` | Extra iCal links (see above) and how many days to load. |
| `night_dim` | When to dim the screen, and how bright it stays (`opacity`). |
| `rotate_seconds` | How long each panel stays up. The two cards take turns. |
| `markets` | Truncgil codes: `USD`, `EUR`, `GBP`, `GRA` (gram gold), `CEYREKALTIN` (quarter gold), … |
| `crypto` | CoinGecko ids: `bitcoin`, `ethereum`, `solana`, … |
| `stocks` | `api_key` (Twelve Data) and `symbols`. Hidden until a key is set. |
| `reminders` | `enabled`, `lists` (empty = all lists), `days_ahead`. |
| `spotify` | `enabled` (default `true`). |
| `kickoff_reminder_minutes` | Alert this many minutes before a followed team plays (0 = off). |
| `night_clock` | `"off"` (only with the moon button) or `"auto"` (also during `night_dim` hours). |
| `goal_alerts` | `"followed"` (your teams only; in basketball, clubs with the same name as a team you follow) or `"all"` (every live game, including all tennis matches). |
| `news` | Groups of RSS feeds, each with a `name` and `feeds`. Optional `exclude` hides headlines containing any of those words. |
| `football` | ESPN codes: `tur.1`, `eng.1`, `esp.1`, `ger.1`, `ita.1`, `fra.1`, `uefa.champions`, `uefa.europa`, `uefa.europa.conf`. |
| `tennis` | `atp`, `wta`. |
| `basketball` | `E` (EuroLeague), `U` (EuroCup). |

Restart the server after editing config: `launchctl kickstart -k gui/$(id -u)/com.sidescreen.server`.

Other Turkish feeds that work: `https://www.hurriyet.com.tr/rss/gundem`, `https://www.aa.com.tr/tr/rss/default?cat=guncel`, `https://www.trthaber.com/manset_articles.rss`.

## How it works

- `server.py`: Python 3.9+ standard library only, listening on `127.0.0.1` only.
  - `POST /api/hook`: Claude Code hook events. Always answers `204` so it never blocks a session.
  - `GET /api/status`: sessions, sorted waiting → error → working → done → idle. Sessions idle for 8 h are dropped.
  - `GET /api/markets`, `/api/news`, `/api/football`, `/api/basketball`, `/api/tennis`, `/api/calendar`: cached on the server (rates 1 min, crypto 2 min, 30-day trends 3–6 h, news 10 min, scores 30 s while live / 5 min otherwise (basketball 45 s / 5 min), Apple Calendar 5 min, tables 10 min, fixtures 1 h, rankings 6 h, calendar 5 min). If a source is down, the last good data is kept.
  - `GET /api/spotify`, `POST /api/spotify` (`{"cmd": "playpause" | "next" | "previous"}`, same rules as prefs): what Spotify is playing, and its controls.
  - `GET /api/myteams`, `/api/reminders`: followed teams' positions and next fixtures; Apple Reminders (read in the background every 3 minutes).
  - `GET /api/usage`: today's Claude Code token use.
  - `GET /api/settings`: what the settings panel can change, and the current values.
  - `GET`/`POST /api/prefs`: followed teams and players, and settings, saved to `prefs.json`. Writes are only accepted as JSON from the dashboard's own address.
  - `GET /api/config`: public settings only.
- `static/index.html`: the whole dashboard. Weather comes straight from Open-Meteo.
- `scripts/hooks.py`: adds/removes the hooks in `settings.json`.
- `scripts/open-display.sh` + `scripts/screen-frame.js`: find the portable screen and open Chrome on it.
- `scripts/reminders.js`, `scripts/spotify.js`: read Reminders and Spotify
- `scripts/calendar-helper/`: the Sidescreen Calendar helper app (Swift, EventKit) that reads your calendars (JavaScript for Automation, run with `osascript`).

Data sources (no API keys): [Open-Meteo](https://open-meteo.com) for weather, [Truncgil Finans](https://finans.truncgil.com) for TL rates and gold, [Frankfurter](https://frankfurter.dev) (ECB) for 30-day currency trends, [CoinGecko](https://www.coingecko.com) for crypto and the gold trend (PAX Gold, one token per troy ounce, as a stand-in), ESPN's public site API for football, tennis and logos, the official EuroLeague API for basketball, and publishers' RSS feeds for news. Stocks use Twelve Data with your key. Truncgil and ESPN are unofficial and could change without notice.
