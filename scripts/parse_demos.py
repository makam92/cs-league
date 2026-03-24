"""
CS League Demo Parser
Parses .dem files, extracts player stats, and caches results so demos are never re-parsed.

Usage:
  python parse_demos.py --season s8
"""

import os
import re
import json
import hashlib
import argparse
import datetime
from pathlib import Path
from demoparser2 import DemoParser

ROOT = Path(__file__).parent.parent


# ── Helpers ───────────────────────────────────────────────────────────────────
def file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_filename(name: str) -> dict:
    """Extract date, map, and team names from demo filename."""
    stem = name.replace(".dem", "")
    # e.g. 2025-09-25_20-01-53_35_de_dust2_Boulder_Mozarteum_vs_Leadstar_Media_AB
    m = re.match(
        r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_(\d+)_(de_[a-z0-9]+)_(.+?)_vs_(.+)$",
        stem,
    )
    if not m:
        return {"date": None, "map": None, "team1_raw": None, "team2_raw": None, "match_id": None}
    date_str, time_str, match_id, map_name, team1, team2 = m.groups()
    return {
        "date":      date_str,
        "time":      time_str.replace("-", ":"),
        "match_id":  match_id,
        "map":       map_name,
        "team1_raw": team1.replace("_", " "),
        "team2_raw": team2.replace("_", " "),
    }


# ── Core parser ───────────────────────────────────────────────────────────────
def _event(parser, name: str):
    """parse_event wrapper — returns a DataFrame even when demoparser2 yields []."""
    import pandas as pd
    df = parser.parse_event(name)
    if isinstance(df, list):
        return pd.DataFrame()
    return df


def _ticks(parser, fields: list, ticks: list):
    """parse_ticks wrapper — returns a DataFrame even when demoparser2 yields []."""
    import pandas as pd
    df = parser.parse_ticks(fields, ticks=ticks)
    if isinstance(df, list):
        return pd.DataFrame()
    return df


def parse_demo(path: Path) -> dict:
    parser = DemoParser(str(path))
    meta   = parse_filename(path.name)

    # ── Team assignments at an early tick ────────────────────────────────────
    # Try progressively later ticks until we find players assigned to teams.
    ticks = None
    for probe_tick in [500, 1000, 2000, 5000, 10000, 20000]:
        candidate = _ticks(parser, ["team_name", "steamid", "name"], ticks=[probe_tick])
        if len(candidate) > 0:
            ticks = candidate
            break
    if ticks is None:
        import pandas as pd
        ticks = pd.DataFrame()
    team_t  = {}  # steamid -> name for T side at start
    team_ct = {}
    for row in ticks.to_dict("records") if len(ticks) else []:
        sid  = str(row["steamid"])
        name = row["name"]
        if row["team_name"] == "TERRORIST":
            team_t[sid]  = name
        elif row["team_name"] == "CT":
            team_ct[sid] = name

    all_players = {**team_t, **team_ct}   # steamid -> name

    # ── Actual game start tick (excludes warmup + knife round) ───────────────
    # round_announce_match_start fires twice: before knife round and after.
    # The second (max) tick marks when the actual game begins.
    match_start_tick = 0
    try:
        announce_df = _event(parser, "round_announce_match_start")
        if len(announce_df) >= 2:
            match_start_tick = int(announce_df["tick"].max())
    except Exception:
        pass
    if match_start_tick == 0:
        try:
            freeze_df        = _event(parser, "round_freeze_end")
            match_start_tick = int(freeze_df["tick"].min()) if len(freeze_df) else 0
        except Exception:
            match_start_tick = 0

    # ── Competitive-match sides (post-knife) ─────────────────────────────────
    # Re-read team assignments at match_start_tick to get the correct sides
    # after any knife-round side swap.
    comp_team_t  = {}
    comp_team_ct = {}
    if match_start_tick > 0:
        comp_ticks = _ticks(parser, ["team_name", "steamid", "name"], ticks=[match_start_tick])
        for row in comp_ticks.to_dict("records") if len(comp_ticks) else []:
            sid = str(row["steamid"])
            if row["team_name"] == "TERRORIST":
                comp_team_t[sid]  = row["name"]
            elif row["team_name"] == "CT":
                comp_team_ct[sid] = row["name"]
    if not comp_team_t and not comp_team_ct:
        # Fallback to early-tick sides if match_start_tick read failed
        comp_team_t  = team_t
        comp_team_ct = team_ct

    # ── Rounds (knife round filtered out) ────────────────────────────────────
    rounds_df = _event(parser, "round_end")
    if len(rounds_df) and "tick" in rounds_df.columns:
        rounds_df = rounds_df[rounds_df["tick"] >= match_start_tick]
    total_rounds = len(rounds_df)

    # Compute team scores using position-based halftime (MR12 regulation + MR3 OT).
    # Position 0-11 = H1 (T-start team is T), 12-23 = H2 (T-start team is CT).
    # OT alternates every 3 rounds starting at position 24.
    rounds_sorted = rounds_df.sort_values("tick") if "tick" in rounds_df.columns else rounds_df
    round_records = rounds_sorted.to_dict("records")

    t_start_score  = 0
    ct_start_score = 0
    for pos, row in enumerate(round_records):
        winner = row["winner"]
        if pos < 12:
            t_is_t = True    # regulation H1
        elif pos < 24:
            t_is_t = False   # regulation H2
        else:
            ot_half_idx = (pos - 24) // 3
            t_is_t = (ot_half_idx % 2 == 0)

        if t_is_t:
            if winner == "T":  t_start_score  += 1
            else:              ct_start_score += 1
        else:
            if winner == "CT": t_start_score  += 1
            else:              ct_start_score += 1

    ct_wins = int((rounds_df["winner"] == "CT").sum()) if "winner" in rounds_df.columns else 0
    t_wins  = int((rounds_df["winner"] == "T").sum())  if "winner" in rounds_df.columns else 0

    # ── Kills / Deaths / Assists / HS ────────────────────────────────────────
    kills_df = _event(parser, "player_death")
    if len(kills_df) and "tick" in kills_df.columns:
        kills_df = kills_df[kills_df["tick"] >= match_start_tick]

    stats = {sid: {
        "name":          name,
        "steamid":       sid,
        "kills":         0,
        "deaths":        0,
        "assists":       0,
        "headshots":     0,
        "flash_assists": 0,
        "damage":        0,
        "rounds":        total_rounds,
        "team_side":     "T" if sid in team_t else "CT",
    } for sid, name in all_players.items()}

    for row in kills_df.to_dict("records"):
        attacker = str(row["attacker_steamid"]) if row["attacker_steamid"] else None
        victim   = str(row["user_steamid"])     if row["user_steamid"]     else None
        assister = str(row["assister_steamid"]) if row["assister_steamid"] else None

        if attacker and attacker in stats and attacker != victim:
            stats[attacker]["kills"]     += 1
            if row["headshot"]:
                stats[attacker]["headshots"] += 1

        if victim and victim in stats:
            stats[victim]["deaths"] += 1

        if assister and assister in stats:
            stats[assister]["assists"]       += 1
            if row.get("assistedflash"):
                stats[assister]["flash_assists"] += 1

    # ── Damage (enemies only, no friendly fire, no overkill) ─────────────────
    # Track each player's HP per round so we never credit more than their
    # remaining health (dmg_health can include overkill).
    # HP resets to 100 for all players at each round_freeze_end.
    freeze_df = _event(parser, "round_freeze_end")
    if len(freeze_df) and "tick" in freeze_df.columns:
        freeze_df = freeze_df[freeze_df["tick"] >= match_start_tick].sort_values("tick")
    freeze_ticks = freeze_df["tick"].tolist() if len(freeze_df) else []

    hurt_df = _event(parser, "player_hurt")
    if len(hurt_df) and "tick" in hurt_df.columns:
        hurt_df = hurt_df[hurt_df["tick"] >= match_start_tick].sort_values("tick")

    current_hp  = {}   # steamid -> current HP this round
    freeze_idx  = 0    # pointer into freeze_ticks

    for row in hurt_df.to_dict("records"):
        tick     = row["tick"]
        attacker = str(row["attacker_steamid"]) if row["attacker_steamid"] else None
        victim   = str(row["user_steamid"])     if row["user_steamid"]     else None

        # Advance HP resets for any freeze_end that has passed
        while freeze_idx < len(freeze_ticks) and tick >= freeze_ticks[freeze_idx]:
            current_hp.clear()
            freeze_idx += 1

        if not attacker or not victim or attacker == victim:
            continue
        if attacker not in stats:
            continue
        # Skip friendly fire: both on same initial team
        if (attacker in team_t and victim in team_t) or (attacker in team_ct and victim in team_ct):
            continue

        victim_hp  = current_hp.get(victim, 100)
        actual_dmg = min(row["dmg_health"], victim_hp)
        current_hp[victim] = victim_hp - actual_dmg
        stats[attacker]["damage"] += actual_dmg

    # ── Determine which side each filename-team was on ────────────────────────
    # We match team1_raw / team2_raw against the folder name to figure out
    # which group of Steam IDs belongs to which team. The folder name is
    # the authoritative team label for this set of demos.
    folder_team = path.parent.name  # e.g. "Boulder Mozarteum"

    # Map scores to team names: team_t started as T, team_ct started as CT
    t_score  = int(t_start_score)
    ct_score = int(ct_start_score)

    return {
        "file":         path.name,
        "folder_team":  folder_team,
        "date":         meta["date"],
        "time":         meta.get("time"),
        "match_id":     meta["match_id"],
        "map":          meta["map"],
        "team1_raw":    meta["team1_raw"],
        "team2_raw":    meta["team2_raw"],
        "total_rounds": total_rounds,
        "ct_wins":      int(ct_wins),
        "t_wins":       int(t_wins),
        # Score per starting side
        "t_start_score":  t_score,
        "ct_start_score": ct_score,
        "team_t_ids":      list(team_t.keys()),
        "team_ct_ids":     list(team_ct.keys()),
        "comp_team_t_ids":  list(comp_team_t.keys()),
        "comp_team_ct_ids": list(comp_team_ct.keys()),
        "player_stats": list(stats.values()),
    }


# ── Team assignment ───────────────────────────────────────────────────────────
def build_team_map(parsed_demos: list) -> dict:
    """
    Returns a dict of steamid -> canonical_team_name.

    Strategy (per folder):
      1. For each demo, try to determine which physical side (T/CT) the filename
         team1 is on, by matching team-name keywords against player display names.
         e.g. "team HailoKnight" → the side whose player is named "HailoKnight".
      2. For each demo, check whether team1_raw or team2_raw matches the folder
         team name (keyword overlap).
      3. Combine signals with a majority vote to learn: which physical side is
         the folder (home) team on?  Apply consistently to ALL demos in the folder.
      4. If neither signal is useful (e.g. completely generic team names AND no
         folder keywords in filename), fall back to co-appearance frequency.
      5. Opponent players get the raw filename name; if that name matches a
         canonical schedule name it gets replaced later (post-processing).
    """
    import re
    from collections import Counter

    def kw(s: str) -> set:
        """Lowercase word tokens, filtering short/common stop words."""
        return {w for w in re.split(r"\W+", s.lower())
                if len(w) >= 3 and w not in ("team", "the", "and", "for", "ab")}

    def team1_side_by_names(d: dict) -> str | None:
        """Which side (T/CT) does team1_raw correspond to, judged by player names?"""
        t1_kw = kw(d["team1_raw"] or "")
        if not t1_kw:
            return None
        t_names  = {ps["name"].lower() for ps in d["player_stats"] if ps.get("team_side") == "T"}
        ct_names = {ps["name"].lower() for ps in d["player_stats"] if ps.get("team_side") == "CT"}
        t_hits   = sum(1 for w in t1_kw if any(w in n for n in t_names))
        ct_hits  = sum(1 for w in t1_kw if any(w in n for n in ct_names))
        if t_hits > ct_hits:  return "T"
        if ct_hits > t_hits:  return "CT"
        return None

    def folder_in_team1(d: dict, folder_kw: set) -> bool | None:
        """Is the folder team named as team1 in the filename?"""
        t1_kw = kw(d["team1_raw"] or "")
        t2_kw = kw(d["team2_raw"] or "")
        s1 = len(t1_kw & folder_kw)
        s2 = len(t2_kw & folder_kw)
        if s1 > s2: return True
        if s2 > s1: return False
        return None

    def majority(votes: list) -> object:
        """Return the majority value from a list, ignoring Nones."""
        counts = Counter(v for v in votes if v is not None)
        return counts.most_common(1)[0][0] if counts else None

    # ── Group demos by folder ─────────────────────────────────────────────────
    folder_groups: dict = {}
    for demo in parsed_demos:
        folder_groups.setdefault(demo["folder_team"], []).append(demo)

    canonical: dict = {}   # steamid → canonical (folder) team name

    for folder_team, demos in folder_groups.items():
        folder_kw_ = kw(folder_team)

        # Per-demo signals
        t1_sides   = [team1_side_by_names(d) for d in demos]
        fit1_votes = [folder_in_team1(d, folder_kw_) for d in demos]

        # Global majority across demos
        team1_side_global   = majority(t1_sides)    # 'T', 'CT', or None
        folder_is_t1_global = majority(fit1_votes)  # True, False, or None

        # Determine the home side for each demo
        if folder_is_t1_global is not None and team1_side_global is not None:
            # We know: folder = team1 (or team2), and team1 starts on T or CT.
            # home_side = which side has the folder team.
            if folder_is_t1_global:
                home_side = team1_side_global  # folder = team1; team1 starts on T or CT
            else:
                home_side = "CT" if team1_side_global == "T" else "T"
            # Apply the same home_side consistently to all demos
            for d in demos:
                home_ids = d["team_t_ids"] if home_side == "T" else d["team_ct_ids"]
                for sid in home_ids:
                    canonical[sid] = folder_team

        else:
            # Fallback: use co-appearance frequency (original heuristic)
            appearances: Counter = Counter()
            for d in demos:
                for sid in d["team_t_ids"] + d["team_ct_ids"]:
                    appearances[sid] += 1
            if not appearances:
                continue
            max_app   = max(appearances.values())
            threshold = max(2, max_app // 2)
            core      = {sid for sid, n in appearances.items() if n >= threshold}
            for d in demos:
                t_core   = sum(1 for s in d["team_t_ids"]  if s in core)
                c_core   = sum(1 for s in d["team_ct_ids"] if s in core)
                home_ids = d["team_t_ids"] if t_core >= c_core else d["team_ct_ids"]
                for sid in home_ids:
                    canonical[sid] = folder_team

    # ── Fill gaps: opponent players get raw filename name ─────────────────────
    team_map: dict = {}

    for demo in parsed_demos:
        folder_team = demo["folder_team"]
        folder_kw_  = kw(folder_team)

        # Which side has the home players in this demo?
        t_home = sum(1 for s in demo["team_t_ids"]  if canonical.get(s) == folder_team)
        c_home = sum(1 for s in demo["team_ct_ids"] if canonical.get(s) == folder_team)
        away_ids = set(demo["team_ct_ids"] if t_home >= c_home else demo["team_t_ids"])

        # Best-guess opponent name from filename
        t1, t2 = demo["team1_raw"] or "", demo["team2_raw"] or ""
        t1_score = len(kw(t1) & folder_kw_)
        t2_score = len(kw(t2) & folder_kw_)
        if t1_score != t2_score:
            opp_name = t2 if t1_score >= t2_score else t1
        else:
            opp_name = t1 if len(kw(t1)) <= len(kw(t2)) else t2

        for sid in away_ids:
            if sid not in canonical:
                team_map.setdefault(sid, opp_name or "Unknown")

    # ── Canonical always wins ─────────────────────────────────────────────────
    team_map.update(canonical)
    return team_map


# ── Aggregation ───────────────────────────────────────────────────────────────
def _canonical_teams(demo: dict, team_map: dict) -> dict:
    """Return home_team / away_team using canonical names from team_map."""
    folder = demo["folder_team"]
    t_ids  = demo["team_t_ids"]
    ct_ids = demo["team_ct_ids"]

    # Which side has the folder (home) team?
    t_home  = sum(1 for s in t_ids  if team_map.get(s) == folder)
    ct_home = sum(1 for s in ct_ids if team_map.get(s) == folder)
    away_ids = ct_ids if t_home >= ct_home else t_ids

    # Canonical away team = most common non-folder team name among away players
    from collections import Counter
    away_names = [team_map[s] for s in away_ids if s in team_map and team_map[s] != folder]
    away_team  = Counter(away_names).most_common(1)[0][0] if away_names else (demo["team2_raw"] or "Unknown")

    return {"home_team": folder, "away_team": away_team}


def aggregate(parsed_demos: list, season_num: int = 7) -> dict:
    # Exclude inconclusive games (neither team reached winning threshold)
    parsed_demos = [d for d in parsed_demos if max(d["t_start_score"], d["ct_start_score"]) >= 13]

    players   = {}   # steamid -> aggregated stats
    steam_ids = set()
    team_map  = build_team_map(parsed_demos)

    for demo in parsed_demos:
        for ps in demo["player_stats"]:
            sid = ps["steamid"]
            steam_ids.add(sid)
            if sid not in players:
                players[sid] = {
                    "steamid":       sid,
                    "name":          ps["name"],
                    "team":          team_map.get(sid, "Unknown"),
                    "kills":         0,
                    "deaths":        0,
                    "assists":       0,
                    "headshots":     0,
                    "flash_assists": 0,
                    "damage":        0,
                    "rounds":        0,
                    "maps":          0,
                }
            p = players[sid]
            p["name"]          = ps["name"]   # keep latest name
            p["kills"]         += ps["kills"]
            p["deaths"]        += ps["deaths"]
            p["assists"]       += ps["assists"]
            p["headshots"]     += ps["headshots"]
            p["flash_assists"] += ps["flash_assists"]
            p["damage"]        += ps["damage"]
            p["rounds"]        += ps["rounds"]
            p["maps"]          += 1
    # Derived stats — computed from accumulated totals so they match K/D/rounds columns
    for p in players.values():
        p["kd"]     = round(p["kills"] / p["deaths"], 2) if p["deaths"] else float(p["kills"])
        p["adr"]    = round(p["damage"] / p["rounds"], 1) if p["rounds"] else 0
        p["hs_pct"] = round(p["headshots"] / p["kills"] * 100, 1) if p["kills"] else 0
        p["kpr"]    = round(p["kills"] / p["rounds"], 2) if p["rounds"] else 0

    return {
        "season":     season_num,
        "players":    list(players.values()),
        "steam_ids":  sorted(steam_ids),
        "demos":      [{
            "file":         d["file"],
            "date":         d["date"],
            "map":          d["map"],
            "match_id":     d["match_id"],
            "team1_raw":    d["team1_raw"],
            "team2_raw":    d["team2_raw"],
            **_canonical_teams(d, team_map),
            "total_rounds":   d["total_rounds"],
            "t_start_score":  d["t_start_score"],
            "ct_start_score": d["ct_start_score"],
            "folder_team":    d["folder_team"],
            "team_t_ids":      d["team_t_ids"],
            "team_ct_ids":     d["team_ct_ids"],
            "comp_team_t_ids":  d.get("comp_team_t_ids", d["team_t_ids"]),
            "comp_team_ct_ids": d.get("comp_team_ct_ids", d["team_ct_ids"]),
            "player_stats": [{
                **ps,
                "kd":     round(ps["kills"] / ps["deaths"], 2) if ps["deaths"] else ps["kills"],
                "hs_pct": round(ps["headshots"] / ps["kills"] * 100, 1) if ps["kills"] else 0,
                "adr":    round(ps["damage"] / ps["rounds"], 1) if ps["rounds"] else 0,
                "kpr":    round(ps["kills"] / ps["rounds"], 2) if ps["rounds"] else 0,
            } for ps in d["player_stats"]],
        } for d in parsed_demos],
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="CS League Demo Parser")
    parser.add_argument("--season", default="s8", help="Season identifier, e.g. s8")
    args = parser.parse_args()

    season    = args.season.lower()
    season_num = int(season[1:]) if season[1:].isdigit() else 0

    DEMOS_DIR  = ROOT / "demos" / season
    DATA_DIR   = ROOT / "data"  / season
    PARSED_LOG = DATA_DIR / "parsed.json"
    STATS_FILE = DATA_DIR / "stats.json"

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not DEMOS_DIR.exists():
        print(f"Demos directory not found: {DEMOS_DIR}")
        return

    print(f"Season: {season}  |  demos: {DEMOS_DIR}  |  data: {DATA_DIR}")

    # Load parse log
    if PARSED_LOG.exists():
        with open(PARSED_LOG) as f:
            parsed_log = json.load(f)   # {filepath: {hash, parsed_at, data}}
    else:
        parsed_log = {}

    demo_files = sorted(DEMOS_DIR.rglob("*.dem"))
    print(f"Found {len(demo_files)} demo(s)")

    # Build set of already-seen hashes to catch duplicates at different paths
    seen_hashes = {v["hash"] for v in parsed_log.values() if "hash" in v}

    changed = False
    for demo_path in demo_files:
        key  = str(demo_path.relative_to(ROOT))
        fhsh = file_hash(demo_path)

        if key in parsed_log and parsed_log[key]["hash"] == fhsh:
            print(f"  [skip]  {demo_path.name}")
            continue

        if fhsh in seen_hashes:
            print(f"  [dup]   {demo_path.name}  (same content already parsed at another path — skipping)")
            continue

        print(f"  [parse] {demo_path.name} ...", end=" ", flush=True)
        try:
            result = parse_demo(demo_path)
            parsed_log[key] = {
                "hash":      fhsh,
                "parsed_at": datetime.datetime.utcnow().isoformat(),
                "data":      result,
            }
            seen_hashes.add(fhsh)
            changed = True
            print("ok")
        except Exception as e:
            print(f"ERROR: {e}")

    if changed:
        with open(PARSED_LOG, "w") as f:
            json.dump(parsed_log, f, indent=2)
        print(f"\nSaved parse log → {PARSED_LOG}")

    # Aggregate all parsed demos
    all_demos = [v["data"] for v in parsed_log.values() if "data" in v]
    stats     = aggregate(all_demos, season_num)

    # ── Post-process: canonicalize team names using schedule data ─────────────
    schedule_file = DATA_DIR / "schedule.json"
    if schedule_file.exists():
        import difflib
        with open(schedule_file) as f:
            schedule = json.load(f)
        canonical_names = sorted({
            name
            for matches in schedule.values()
            for m in matches
            for name in (m["team1"], m["team2"])
        })

        def normalize(s: str) -> str:
            """Lowercase, drop 'AB'/'team ' noise, collapse spaces."""
            s = s.lower()
            s = re.sub(r"\bteam\b", "", s)
            s = re.sub(r"\bab\b", "", s)
            return re.sub(r"\s+", " ", s).strip()

        norm_canonical = {normalize(n): n for n in canonical_names}

        def best_match(raw: str) -> str | None:
            nr = normalize(raw)
            # Exact after normalization
            if nr in norm_canonical:
                return norm_canonical[nr]
            # Substring containment (both ways, min 5 chars)
            if len(nr) >= 5:
                for nc, canon in norm_canonical.items():
                    if nr in nc or nc in nr:
                        return canon
            # Sequence similarity fallback
            close = difflib.get_close_matches(nr, norm_canonical.keys(), n=1, cutoff=0.75)
            return norm_canonical[close[0]] if close else None

        # Hard-coded aliases for in-game team names that can't be auto-matched
        ALIASES: dict[str, str] = {
            "BoaBots": "NoA Ignite AB",
        }

        # Only remap players whose current team is NOT already a canonical name
        canonical_set = set(canonical_names)
        for p in stats["players"]:
            if p["team"] in ALIASES:
                p["team"] = ALIASES[p["team"]]
            elif p["team"] not in canonical_set:
                matched = best_match(p["team"])
                if matched:
                    p["team"] = matched

    # ── Post-process: roster-based fuzzy matching ─────────────────────────────
    # Use authoritative SFL roster (nickname → team) to correct assignments.
    # This catches cases where demo filenames use player nicknames as team names
    # (e.g. "team Martengooz" → Omegapoint AB, "team Kimchi" → Xenit AB).
    roster_file = DATA_DIR / "rosters.json"
    if roster_file.exists():
        import difflib
        with open(roster_file, encoding="utf-8") as f:
            rosters = json.load(f)

        # Build reverse map: lowercase nickname → canonical team name
        nick_to_team: dict = {}
        for team_name, nicks in rosters.items():
            for nick in nicks:
                nick_to_team[nick.lower()] = team_name

        all_nicks = list(nick_to_team.keys())

        def match_nick(name: str) -> str | None:
            """Fuzzy-match a player display name against roster nicknames."""
            name_lc = name.lower().strip()
            # Exact match
            if name_lc in nick_to_team:
                return nick_to_team[name_lc]
            # Substring containment (name contains nickname or vice versa)
            for nick in all_nicks:
                if (len(nick) >= 3 and nick in name_lc) or (len(name_lc) >= 3 and name_lc in nick):
                    return nick_to_team[nick]
            # Fuzzy match
            close = difflib.get_close_matches(name_lc, all_nicks, n=1, cutoff=0.82)
            return nick_to_team[close[0]] if close else None

        def match_team_name_via_nick(raw_team: str) -> str | None:
            """For 'team X' style names, try matching X as a nickname."""
            stripped = re.sub(r"(?i)^team\s+", "", raw_team).strip()
            if stripped:
                return match_nick(stripped)
            return None

        canonical_set_now = set(rosters.keys())
        corrections = 0
        for p in stats["players"]:
            current_team = p["team"]
            if current_team in canonical_set_now:
                continue  # Already canonical — also verify player belongs to this roster

            # Try to match via team name keyword (e.g. "team Martengooz")
            matched = match_team_name_via_nick(current_team)
            if not matched:
                # Try matching player's own display name against roster nicknames
                matched = match_nick(p["name"])
            if matched and matched != current_team:
                print(f"  [roster] {p['name']} ({current_team}) → {matched}")
                p["team"] = matched
                corrections += 1

        # Also verify players assigned to canonical teams are in the right roster
        for p in stats["players"]:
            current_team = p["team"]
            if current_team not in canonical_set_now:
                continue
            roster_nicks = [n.lower() for n in rosters.get(current_team, [])]
            if not roster_nicks:
                continue
            # If player's name matches a nick in a DIFFERENT team, reassign
            matched = match_nick(p["name"])
            if matched and matched != current_team:
                print(f"  [roster fix] {p['name']} ({current_team}) → {matched}")
                p["team"] = matched
                corrections += 1

        print(f"Roster-based corrections: {corrections}")

    # ── Rebuild home_team / away_team using final corrected player assignments ──
    sid_to_team = {p["steamid"]: p["team"] for p in stats["players"]}
    from collections import Counter as _Counter
    for d in stats["demos"]:
        home   = d.get("folder_team", "")
        # Use competitive sides (post-knife) for score attribution
        t_ids  = d.get("comp_team_t_ids") or d.get("team_t_ids", [])
        ct_ids = d.get("comp_team_ct_ids") or d.get("team_ct_ids", [])
        t_home  = sum(1 for s in t_ids  if sid_to_team.get(s) == home)
        ct_home = sum(1 for s in ct_ids if sid_to_team.get(s) == home)
        home_on_t = t_home >= ct_home
        away_ids   = ct_ids if home_on_t else t_ids
        away_names = [sid_to_team[s] for s in away_ids if sid_to_team.get(s) and sid_to_team.get(s) != home]
        d["home_team"]  = home
        d["away_team"]  = _Counter(away_names).most_common(1)[0][0] if away_names else (d.get("team2_raw") or "Unknown")
        d["home_score"] = d["t_start_score"] if home_on_t else d["ct_start_score"]
        d["away_score"] = d["ct_start_score"] if home_on_t else d["t_start_score"]

    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(f"Aggregated {len(all_demos)} demo(s), {len(stats['players'])} unique players")
    print(f"Saved stats → {STATS_FILE}")
    print(f"\nAll Steam IDs found ({len(stats['steam_ids'])}):")
    for sid in stats["steam_ids"]:
        print(f"  {sid}")


if __name__ == "__main__":
    main()
