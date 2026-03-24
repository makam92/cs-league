# CS League — Project Context

## What this is
A web app for tracking CS2 match stats for Svenska Företagsligan (SFL) Season 8.
Parses `.dem` demo files, extracts player stats, fetches FACEIT ELO, and displays standings/stats on a static site hosted at https://makam92.github.io/cs-league/

## Site pages
- `index.html` — FACEIT ELO rankings by team
- `standings.html` — Division standings
- `stats.html` — Individual player stats
- `match-history.html` — Per-demo match results with expandable player stats

## Data pipeline
All data lives in `data/s8/`:
- `parsed.json` — raw parse cache (keyed by filepath+hash, never re-parses same demo)
- `stats.json` — aggregated player stats with correct team assignments
- `elo_teams.json` — FACEIT ELO per team
- `standings.json` — division standings
- `schedule.json` — match schedule from toornament widget
- `rosters.json` — authoritative SFL roster (nickname → team), parsed from SFL HTML page

## Scripts
- `scripts/parse_demos.py --season s8` — parse demos, aggregate stats, canonicalize team names
- `scripts/fetch_elo.py --season s8` — fetch FACEIT ELO for all players in stats.json
- `scripts/fetch_schedule.py` — fetch match schedule from toornament widget
- `scripts/fetch_standings.py` — fetch division standings

## Adding new demos workflow
1. Drop new `.dem` files into `demos/s8/<TeamFolderName>/`
2. `python3 scripts/parse_demos.py --season s8`
3. `python3 scripts/fetch_elo.py --season s8`
4. `git add data/s8/ && git commit -m "Update s8 data" && git push`

Already-parsed demos are skipped automatically (by filepath+hash). Duplicate files at different paths are also detected and skipped.

## Team assignment logic (parse_demos.py)
Team names in demo filenames are often wrong or use player nicknames (e.g. "team Kimchi", "team Martengooz"). The pipeline uses three layers to resolve correct team assignments:

1. **`build_team_map()`** — two-signal approach per folder:
   - Matches team name keywords from filename against player display names to find which side (T/CT) each team is on
   - Matches folder name against filename team names to determine which side is the home team
   - Majority vote across demos in the same folder

2. **Schedule canonicalization** — fuzzy-matches raw team names against canonical names from `schedule.json` (cutoff 0.75)

3. **Roster correction** — loads `rosters.json` and matches player Steam display names against official SFL nicknames. Matching logic:
   - Exact match first
   - Word-boundary containment in either direction (e.g. "neoo" matches "Uniguide neoo" but "mrk" does NOT match "mrkothe")
   - Only accepts a match if exactly one team's roster matches (ambiguous = skip)
   - Fuzzy match fallback (cutoff 0.82)

## rosters.json notes
- Source of truth is the SFL homepage HTML — re-paste it when rosters change
- Stand-in players (not on official SFL roster) must be added manually using their in-game Steam display name
- If a player's display name has a clan prefix (e.g. "Uniguide neoo"), the word-boundary match handles it automatically — no need to add the full prefixed name
- Known stand-ins added manually: `Lord Commander` (Andwhy AB), `spetero` (Andwhy AB)

## Known aliases
- "BoaBots" → "NoA Ignite AB" (hard-coded in ALIASES dict in parse_demos.py)

## Full data refresh workflow
Run this after adding demos or fixing rosters:
```
python3 scripts/parse_demos.py --season s8
python3 scripts/fetch_elo.py --season s8
python3 scripts/fetch_standings.py
python3 scripts/fetch_schedule.py
git add data/s8/ && git commit -m "Refresh s8 data" && git push
```
Always run `fetch_elo.py` after `parse_demos.py` — ELO is keyed by steamid→team and must reflect the latest team assignments.

## Secrets
- FACEIT API key is stored in `.env` (gitignored), loaded via `os.environ.get("FACEIT_API_KEY")`
- Set it with: `export FACEIT_API_KEY=your_key` or put it in `.env`

## Deployment
- Hosted on GitHub Pages: https://makam92.github.io/cs-league/
- Repo: https://github.com/makam92/cs-league (public)
- Push to `main` to deploy — no build step, pure static site

## Divisions (S8)
- Division 1: 7 teams
- Divisions 2A–2G: 8 teams each
- 64 teams total across all divisions
