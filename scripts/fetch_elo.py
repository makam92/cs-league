"""
Fetches FACEIT ELO for all players found in parsed demos, grouped by team folder.
Outputs data/<season>/elo_teams.json  { team_name: [ {steamid, name, nickname, avatar, elo, level} ] }

Usage:
  python fetch_elo.py --season s8
"""

import json, subprocess, time, argparse
from pathlib import Path
from collections import Counter

ROOT    = Path(__file__).parent.parent
API_KEY = "e19bebec-abc8-4a5b-855a-6eb7ee1e73b6"


def faceit_lookup(steamid: str, cache: dict) -> dict | None:
    if steamid in cache:
        return cache[steamid]
    url = f"https://open.faceit.com/data/v4/players?game=cs2&game_player_id={steamid}"
    result = subprocess.run(
        ["curl", "-s", "-H", f"Authorization: Bearer {API_KEY}", url],
        capture_output=True, text=True, timeout=10
    )
    try:
        data = json.loads(result.stdout)
    except Exception:
        return None
    if "player_id" not in data:
        return None
    entry = {
        "nickname": data.get("nickname", ""),
        "avatar":   data.get("avatar", ""),
        "elo":      data.get("games", {}).get("cs2", {}).get("faceit_elo", 0),
        "level":    data.get("games", {}).get("cs2", {}).get("skill_level", 0),
        "faceit_id": data.get("player_id", ""),
    }
    cache[steamid] = entry
    return entry


def core_players(demos: list, all_demos_by_team: dict) -> list[str]:
    """Return steam IDs of the folder team (not their opponents).

    For each demo we pick whichever side has the higher combined appearance-
    count within this folder — folder-team players appear in many demos while
    each opponent group appears in only one.  When counts tie (single-demo
    folders) we break the tie by checking how many players on each side also
    appear as opponents in *other* team folders; genuine opponents show up in
    other folders' demos, so the side with fewer cross-folder appearances is
    more likely the home team.
    """
    if not demos:
        return []

    # Count how many of *this* folder's demos each player appears in
    counts: Counter = Counter()
    for d in demos:
        for sid in d["team_t_ids"] + d["team_ct_ids"]:
            counts[sid] += 1

    if not counts:
        return []

    # Build set of player IDs that appear in other teams' folders
    this_team = demos[0]["folder_team"]
    other_folder_ids: set = set()
    for team, other_demos in all_demos_by_team.items():
        if team == this_team:
            continue
        for d in other_demos:
            other_folder_ids.update(d["team_t_ids"] + d["team_ct_ids"])

    folder_ids: set = set()
    for d in demos:
        t_score  = sum(counts[s] for s in d["team_t_ids"])
        ct_score = sum(counts[s] for s in d["team_ct_ids"])
        if t_score != ct_score:
            # Clear winner by in-folder appearance count
            side = d["team_t_ids"] if t_score > ct_score else d["team_ct_ids"]
        else:
            # Tie — pick side with fewer players seen in other team folders
            # (home team players are less likely to appear prominently elsewhere)
            t_cross  = sum(1 for s in d["team_t_ids"]  if s in other_folder_ids)
            ct_cross = sum(1 for s in d["team_ct_ids"] if s in other_folder_ids)
            # More cross-folder appearances means this team plays widely in the
            # league — they're more likely to be the established folder team
            side = d["team_t_ids"] if t_cross >= ct_cross else d["team_ct_ids"]
        folder_ids.update(side)

    return [sid for sid, _ in Counter({s: counts[s] for s in folder_ids}).most_common(10)]


def players_from_opponent_demos(
    team_name: str, all_demos: list, known_folder_players: dict
) -> tuple[list, dict]:
    """For teams whose own folder demos are empty, reconstruct their roster by
    looking at demos in other folders where this team appeared as an opponent.

    Strategy: in a demo stored in folder X, X's known players are on one side.
    If the demo filename also mentions `team_name`, the OTHER side must be
    `team_name`.  Falls back to matching player in-game names when the folder
    team is unrecognised.
    """
    keywords = [w.lower() for w in team_name.split() if len(w) > 2]
    counts: Counter = Counter()
    name_map: dict = {}

    for d in all_demos:
        fname  = d["file"].lower()
        t1_raw = (d.get("team1_raw") or "").lower()
        t2_raw = (d.get("team2_raw") or "").lower()
        combined = " ".join([fname, t1_raw, t2_raw])

        if not any(kw in combined for kw in keywords):
            continue

        # Determine which side is the folder (home) team
        folder_known = known_folder_players.get(d["folder_team"], set())
        t_home  = sum(1 for s in d["team_t_ids"]  if s in folder_known)
        ct_home = sum(1 for s in d["team_ct_ids"] if s in folder_known)

        if t_home > ct_home:
            side_ids = d["team_ct_ids"]   # home is T → target team is CT
        elif ct_home > t_home:
            side_ids = d["team_t_ids"]    # home is CT → target team is T
        else:
            # Fallback: check player names for team keyword
            t_names = sum(
                1 for ps in d.get("player_stats", [])
                if ps["steamid"] in d["team_t_ids"]
                and any(kw in ps.get("name", "").lower() for kw in keywords)
            )
            ct_names = sum(
                1 for ps in d.get("player_stats", [])
                if ps["steamid"] in d["team_ct_ids"]
                and any(kw in ps.get("name", "").lower() for kw in keywords)
            )
            if t_names > ct_names:
                side_ids = d["team_t_ids"]
            elif ct_names > t_names:
                side_ids = d["team_ct_ids"]
            else:
                continue  # Cannot determine side

        for sid in side_ids:
            counts[sid] += 1
        for ps in d.get("player_stats", []):
            name_map[ps["steamid"]] = ps["name"]

    if not counts:
        return [], {}
    return [sid for sid, _ in counts.most_common(7)], name_map


def main():
    parser = argparse.ArgumentParser(description="CS League FACEIT ELO Fetcher")
    parser.add_argument("--season", default="s8", help="Season identifier, e.g. s8")
    args = parser.parse_args()

    season = args.season.lower()
    DATA_DIR    = ROOT / "data" / season
    PARSED      = DATA_DIR / "parsed.json"
    ELO_FILE    = DATA_DIR / "elo_teams.json"
    FACEIT_FILE = DATA_DIR / "faceit.json"

    STATS_FILE = DATA_DIR / "stats.json"

    print(f"Season: {season}  |  data: {DATA_DIR}")

    if not STATS_FILE.exists():
        print("No stats.json found — run parse_demos.py first")
        return

    with open(STATS_FILE) as f:
        stats = json.load(f)

    # Load existing faceit cache
    faceit_cache: dict = {}
    if FACEIT_FILE.exists():
        with open(FACEIT_FILE) as f:
            faceit_cache = json.load(f)

    # Build team → {steamid: name} from stats.json (already has correct assignments)
    by_team: dict[str, dict] = {}
    for p in stats.get("players", []):
        team = p.get("team", "Unknown")
        if team and team != "Unknown":
            by_team.setdefault(team, {})[p["steamid"]] = p["name"]

    teams_out: dict[str, list] = {}

    for team, sid_names in sorted(by_team.items()):
        team_players = []
        for sid, name in sid_names.items():
            print(f"  [{team}] {sid} ...", end=" ", flush=True)
            info = faceit_lookup(sid, faceit_cache)
            if info:
                print(f"{info['nickname']} ELO={info['elo']}")
            else:
                print("not found")
            team_players.append({
                "steamid":  sid,
                "name":     name,
                "nickname": info["nickname"] if info else "",
                "avatar":   info["avatar"]   if info else "",
                "elo":      info["elo"]       if info else 0,
                "level":    info["level"]     if info else 0,
                "faceit_id": info["faceit_id"] if info else "",
            })
            time.sleep(0.15)   # gentle rate-limit

        team_players.sort(key=lambda p: -(p["elo"] or 0))
        teams_out[team] = team_players

    with open(ELO_FILE, "w") as f:
        json.dump(teams_out, f, indent=2, ensure_ascii=False)

    # Save updated faceit cache
    with open(FACEIT_FILE, "w") as f:
        json.dump(faceit_cache, f, indent=2, ensure_ascii=False)

    print(f"\nSaved → {ELO_FILE}")


if __name__ == "__main__":
    main()
