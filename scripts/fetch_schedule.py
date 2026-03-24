"""
Fetches the full match schedule from the toornament widget and saves it
grouped by division to data/s8/schedule.json.

Division is inferred from team names using the known S8 roster.

Usage:
  python fetch_schedule.py
"""

import json
import requests
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup

ROOT     = Path(__file__).parent.parent
OUT_FILE = ROOT / "data" / "s8" / "schedule.json"

SCHEDULE_URL = (
    "https://widget.toornament.com/tournaments/2435506856337696767"
    "/matches/schedule/?_locale=en_US&display_timezone=Europe%2FStockholm"
)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cs-league-bot/1.0)"}

S8_DIVISION_TEAMS = {
    "Division 1":  ["CGI Sverige AB","Bredband2 AB","Toyota Material Handling AB","Telia Sverige AB","Netlight AB DUENDE","Northmill Bank AB","Webhallen Sverige AB"],
    "Division 2A": ["Sciber AB","Spiris AB","Fortnox AB","EA DICE AB","Telia AB Jönköping","Netlight AB BOIDS","Boulder AB Hong Kong","XXL Sport och Vildmark AB"],
    "Division 2B": ["Fragbite AB","HappyHomes Lidingö AB","Öhrlings PricewaterhouseCoopers AB","Uniguide AB","Lumera AB 2","Aderian Aktiv IT AB","Xenit AB","Svea Bank AB"],
    "Division 2C": ["Boulder AB Mozarteum","Dizparc Infrastruktur Jönköping AB","Eltel Networks AB","Onevinn AB","SSG Solutions AB","DynaMate AB","Omegapoint AB","Asurgent AB"],
    "Division 2D": ["Accenture AB","Nordlo Evolve AB","evolvit Solutions AB","PARA Esports AB","Silverspin AB","NoA Ignite AB","Horda Stans AB","Teamtailor AB"],
    "Division 2E": ["Lumera AB 1","Nordomatic AB","Teris AB","Viström Digital Development AB","SOLTAK AB","Boulder AB Golden","Monitor ERP System AB","Enzure AB"],
    "Division 2F": ["Etraveli Group AB","CGI AB Optimistic Reloaders","Hello Ebbot AB","Netgain AB","SAVR AB","Hogia AB","Sweco AB","Leadstar Media AB"],
    "Division 2G": ["Softronic CM1 AB","Knuts Bygg AB","Andwhy AB","Gjuteriteknik AB","Infracom AB","Mpya Digital AB","Bizzdo AB","NODAF AB"],
}

# Reverse map: team → division
TEAM_TO_DIV = {}
for div, teams in S8_DIVISION_TEAMS.items():
    for t in teams:
        TEAM_TO_DIV[t] = div


def infer_division(team1: str, team2: str) -> str:
    return TEAM_TO_DIV.get(team1) or TEAM_TO_DIV.get(team2) or "Unknown"


def parse_schedule(html: str) -> list[dict]:
    soup    = BeautifulSoup(html, "html.parser")
    matches = []

    for event in soup.find_all("div", attrs={"data-role": "sch-event"}):
        dt_str  = event.get("data-time", "")
        match   = event.find(class_="match")
        if not match:
            continue

        opp1 = match.find(class_="opponent-1")
        opp2 = match.find(class_="opponent-2")
        if not opp1 or not opp2:
            continue

        name1 = opp1.find(class_="name")
        name2 = opp2.find(class_="name")
        res1  = opp1.find(class_="result")
        res2  = opp2.find(class_="result")

        team1 = name1.get_text(strip=True) if name1 else ""
        team2 = name2.get_text(strip=True) if name2 else ""

        played = res1 is not None and res2 is not None
        score1 = int(res1.get_text(strip=True)) if played and res1.get_text(strip=True).isdigit() else None
        score2 = int(res2.get_text(strip=True)) if played and res2.get_text(strip=True).isdigit() else None

        matches.append({
            "datetime":  dt_str,
            "team1":     team1,
            "team2":     team2,
            "score1":    score1,
            "score2":    score2,
            "played":    played,
            "division":  infer_division(team1, team2),
        })

    return matches


def main():
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("Fetching schedule ...", end=" ", flush=True)
    resp = requests.get(SCHEDULE_URL, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    print("ok")

    matches = parse_schedule(resp.text)
    print(f"Parsed {len(matches)} matches")

    # Group by division
    by_div: dict[str, list] = {}
    for m in matches:
        by_div.setdefault(m["division"], []).append(m)

    for div, ms in by_div.items():
        played   = sum(1 for m in ms if m["played"])
        upcoming = sum(1 for m in ms if not m["played"])
        print(f"  {div}: {played} played, {upcoming} upcoming")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(by_div, f, indent=2, ensure_ascii=False)

    print(f"\nSaved → {OUT_FILE}")


if __name__ == "__main__":
    main()
