"""
Fetch Leetify ratings (aim, utility, positioning, clutch) for all players in stats.json.
Saves to data/{season}/leetify.json keyed by steamid.
Completely separate from faceit.json — will not touch FACEIT data.

Usage:
    python3 scripts/fetch_leetify.py --season s8
"""

import argparse
import json
import ssl
import time
import urllib.request
from pathlib import Path

DATA_BASE = Path(__file__).parent.parent / "data"

# SSL context (Leetify cert chain issue on macOS)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch_leetify(steamid: str) -> dict | None:
    url = f"https://api.leetify.com/api/mini-profiles/{steamid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cs-league-stats/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as r:
            data = json.loads(r.read())
        ratings = data.get("ratings") or {}
        if not ratings.get("leetifyRatingRounds"):
            return None
        return {
            "aim":         round(ratings["aim"], 1),
            "utility":     round(ratings["utility"], 1),
            "positioning": round(ratings["positioning"], 1),
            "clutch":      round(ratings["clutch"], 3),
            "rounds":      ratings["leetifyRatingRounds"],
        }
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="s8")
    args = parser.parse_args()

    data_dir = DATA_BASE / args.season
    stats_file = data_dir / "stats.json"
    out_file   = data_dir / "leetify.json"

    with open(stats_file) as f:
        stats = json.load(f)

    # Load existing cache so we don't re-fetch unnecessarily
    existing: dict = {}
    if out_file.exists():
        with open(out_file) as f:
            existing = json.load(f)

    results = dict(existing)
    found = 0

    for p in stats.get("players", []):
        sid  = p["steamid"]
        name = p.get("name", sid)
        team = p.get("team", "?")

        data = fetch_leetify(sid)
        results[sid] = data
        if data:
            found += 1
            print(f"  [{team}] {name} ... aim={data['aim']} util={data['utility']} pos={data['positioning']} clutch={data['clutch']} ({data['rounds']} rounds)")
        else:
            print(f"  [{team}] {name} ... no data")
        time.sleep(0.3)

    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{found}/{len(results)} players have Leetify ratings.")
    print(f"Saved → {out_file}")


if __name__ == "__main__":
    main()
