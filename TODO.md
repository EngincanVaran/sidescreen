# To do

Ideas for Sidescreen, roughly in the order we plan to build them.

## Next up

### Basketball tab (EuroLeague + EuroCup)
A third tab in the sports card, next to Football and Tennis.
- Today's games with live scores, a standings table, and followed teams (same star picker).
- Alerts for tip-off, end of each quarter and the final score. Not every basket: that would be far too many.
- Source: the official EuroLeague API, no key needed.
  - Games: `https://api-live.euroleague.net/v2/competitions/E/seasons/E2026/games` (scores, quarter scores, club crests).
  - Standings: `https://api-live.euroleague.net/v2/competitions/E/seasons/E2026/rounds/{round}/standings`.
  - EuroCup uses the same API with competition code `U`.

### Turkish Basketball Super League (BSL)
- Blocked on a data source. ESPN doesn't cover it, and tbf.org.tr shows a bot check to scripts (don't try to get around it).
- To do: look for another free source (the league's own site, a public feed). If none, EuroLeague and EuroCup still cover the big Turkish clubs.

### Apple Calendar, read directly
Read the Calendar app the same way as Reminders, so no public iCal link is needed (more private). Keep iCal links as an option.

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
- An iCal link (until Apple Calendar is read directly).
- Try the Spotify pill with the real app and allow "python3 may control Spotify".

## Decided against

- Earthquake alerts.
- Yahoo Finance for stocks (blocked; its workaround counts as getting around a bot check).
