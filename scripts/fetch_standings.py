"""
Fetches live standings for all S8 divisions by scraping the toornament widget HTML.
Saves to data/s8/standings.json

Usage:
  python fetch_standings.py
  python fetch_standings.py --season s8   (default)
"""

import json
import time
import argparse
import requests
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent

TOURNAMENT_ID = "2435506856337696767"

S8_DIVISIONS = [
    {"name": "Division 1",  "stage_id": "2435508538089762815"},
    {"name": "Division 2A", "stage_id": "2435509158745905151"},
    {"name": "Division 2B", "stage_id": "2435509741976918015"},
    {"name": "Division 2C", "stage_id": "2435510493935450111"},
    {"name": "Division 2D", "stage_id": "2435511182950561791"},
    {"name": "Division 2E", "stage_id": "2437182776008648703"},
    {"name": "Division 2F", "stage_id": "2437183572516995071"},
    {"name": "Division 2G", "stage_id": "2437184077165709311"},
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cs-league-bot/1.0)"}


def parse_int(s: str) -> int:
    s = s.strip().lstrip("+")
    try:
        return int(s)
    except ValueError:
        return 0


def fetch_division(stage_id: str) -> list[dict]:
    url = (
        f"https://widget.toornament.com/tournaments/{TOURNAMENT_ID}"
        f"/stages/{stage_id}/?_locale=en_US"
    )
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()

    soup  = BeautifulSoup(resp.text, "html.parser")
    items = soup.find_all(class_="ranking-item")
    if not items:
        return []

    standings = []
    for item in items:
        rank_el = item.find(class_="rank")
        name_el = item.find(class_="name")
        metrics = [m.get_text(strip=True) for m in item.find_all(class_="metric")]

        if not rank_el or not name_el or len(metrics) < 9:
            continue

        # metric order: P, W, D, L, F, SF, SA, +/-, Pts
        standings.append({
            "rank":          parse_int(rank_el.get_text(strip=True)),
            "team":          name_el.get_text(strip=True),
            "played":        parse_int(metrics[0]),
            "wins":          parse_int(metrics[1]),
            "draws":         parse_int(metrics[2]),
            "losses":        parse_int(metrics[3]),
            "forfeits":      parse_int(metrics[4]),
            "score_for":     parse_int(metrics[5]),
            "score_against": parse_int(metrics[6]),
            "diff":          parse_int(metrics[7]),
            "points":        parse_int(metrics[8]),
        })
    return standings


def main():
    parser = argparse.ArgumentParser(description="CS League Standings Fetcher")
    parser.add_argument("--season", default="s8", help="Season identifier (default: s8)")
    args = parser.parse_args()

    season   = args.season.lower()
    data_dir = ROOT / "data" / season
    out_file = data_dir / "standings.json"

    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Season: {season}  |  output: {out_file}\n")

    result: dict[str, list] = {}

    for div in S8_DIVISIONS:
        print(f"  Fetching {div['name']} ...", end=" ", flush=True)
        try:
            standings = fetch_division(div["stage_id"])
            result[div["name"]] = standings
            if standings:
                leader = standings[0]["team"]
                print(f"{len(standings)} teams  (leader: {leader})")
            else:
                print("no data")
        except Exception as e:
            print(f"ERROR: {e}")
            result[div["name"]] = []
        time.sleep(0.4)

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\nSaved → {out_file}")


if __name__ == "__main__":
    main()
