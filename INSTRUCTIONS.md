# CS League — Instructions

## Setup (first time)
1. Install dependencies:
   ```
   pip install demoparser2 pandas requests beautifulsoup4
   ```
2. Create a `.env` file in the project root:
   ```
   FACEIT_API_KEY=your_faceit_api_key_here
   ```

## Adding new demos
1. Drop `.dem` files into `demos/s8/<TeamFolderName>/`
   - The folder name should match the team name (e.g. `demos/s8/Xenit AB/`)
2. Parse demos:
   ```
   python3 scripts/parse_demos.py --season s8
   ```
3. Fetch updated FACEIT ELO:
   ```
   export FACEIT_API_KEY=$(grep FACEIT_API_KEY .env | cut -d= -f2)
   python3 scripts/fetch_elo.py --season s8
   ```
4. Push to deploy:
   ```
   git add data/s8/
   git commit -m "Update s8 data"
   git push
   ```

Already-parsed demos are skipped automatically — safe to re-run after adding new files to an existing week's folder.

## Updating the schedule / standings
```
python3 scripts/fetch_schedule.py
python3 scripts/fetch_standings.py
```

## Updating rosters
If the SFL roster page changes (new players, substitutions):
1. Paste the updated HTML from the SFL page into a new Claude session
2. Claude will re-parse `data/s8/rosters.json`
3. Re-run `parse_demos.py` to apply updated mappings

## Site
Live at: https://makam92.github.io/cs-league/
Repo: https://github.com/makam92/cs-league
