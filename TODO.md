# To do

Ideas for Sidescreen, roughly in the order we plan to build them.

## Later

- **Where to watch**: the TV channel next to each match, if ESPN provides it.
- **Air quality and sunrise/sunset** in the weather card (Open-Meteo).
- **Mac mini health**: CPU, memory, disk and network, as a small panel or a rail line.
- **GitHub card**: open PRs, review requests and CI status (`gh` is already signed in).
- **Formula 1**: next race countdown, results and standings (Jolpica API, free).
- **Unread mail count** from the Mail app. The count only, never message content.
- **Rain nowcast**: "Rain starting in ~20 min" alerts.
- **Match details**: tap a match to see its goals, cards and lineups.
- **Night photo frame**: after the dim time, show photos from an album instead of the dimmed dashboard.

## Waiting on you

- A Twelve Data key in `config.json` to show stocks (check Borsa Istanbul is covered on the free plan).
- Try the Spotify pill with the real app and allow "python3 may control Spotify".

## Done

- Claude Code usage line, follow tennis players, night clock, settings panel.

- Basketball tab: EuroLeague and EuroCup games and standings.
- Apple Calendar read directly through the Sidescreen Calendar helper app (EventKit), iCal links still supported.
- Weather card rotates with reminders and agenda.

## Decided against

- Turkish Basketball Super League (BSL), for now: no free source without a key. Checked 2026-11-08:
  - tbf.org.tr and bsl.org.tr: bot check (don't get around it).
  - ESPN: doesn't cover it.
  - TheSportsDB: complete only on the paid tier; the free key (`123`) returns just 15 games and some scores are missing.
  - API-Basketball (api-sports.io): live scores, fixtures and standings, but needs a free key (100 requests/day). Skipped because it needs a key.
  - Wikipedia's season page: has the table (as a template), but it's updated by hand, days behind, and has no fixtures.
  Turkish clubs in the EuroLeague and EuroCup already show in the basketball tab.

- Earthquake alerts.
- Yahoo Finance for stocks (blocked; its workaround counts as getting around a bot check).
