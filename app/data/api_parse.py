"""API-Football yanıtlarını iç biçime çevirme (saf fonksiyonlar)."""
from __future__ import annotations

from datetime import datetime
from statistics import mean

FINISHED = {"FT", "AET", "PEN"}
POSTPONED = {"PST"}
CANCELLED = {"CANC"}
ABANDONED = {"ABD", "AWD", "WO"}

_BETS = {
    "Match Winner": {"Home": "MS1", "Draw": "MSX", "Away": "MS2"},
    "Double Chance": {"Home/Draw": "CS1X", "Home/Away": "CS12", "Draw/Away": "CSX2"},
    "Goals Over/Under": {"Over 1.5": "UST15", "Under 1.5": "ALT15", "Over 2.5": "UST25", "Under 2.5": "ALT25",
                         "Over 3.5": "UST35", "Under 3.5": "ALT35"},
    "Total - Home": {"Over 0.5": "EVUST05", "Under 0.5": "EVALT05", "Over 1.5": "EVUST15", "Under 1.5": "EVALT15"},
    "Total - Away": {"Over 0.5": "DEPUST05", "Under 0.5": "DEPALT05", "Over 1.5": "DEPUST15", "Under 1.5": "DEPALT15"},
    "Both Teams Score": {"Yes": "KGVAR", "No": "KGYOK"},
}
POSITIONS = {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M", "Attacker": "F"}


def _status(short: str) -> str:
    if short in FINISHED:
        return "finished"
    if short in POSTPONED:
        return "postponed"
    if short in CANCELLED:
        return "cancelled"
    if short in ABANDONED:
        return "abandoned"
    return "scheduled"


def parse_api_fixture(item: dict) -> dict:
    fx, lg, teams = item["fixture"], item["league"], item["teams"]
    status = _status(fx["status"]["short"])
    ft = (item.get("score") or {}).get("fulltime") or {}
    hg, ag = (ft.get("home"), ft.get("away")) if status == "finished" else (None, None)
    return {
        "api_fixture_id": int(fx["id"]),
        "league_id": int(lg["id"]),
        "season": int(lg["season"]),
        "kickoff_utc": datetime.fromisoformat(fx["date"]),
        "home_name": teams["home"]["name"], "home_api_id": int(teams["home"]["id"]),
        "away_name": teams["away"]["name"], "away_api_id": int(teams["away"]["id"]),
        "status": status,
        "home_goals": None if hg is None else int(hg),
        "away_goals": None if ag is None else int(ag),
    }


def parse_api_odds(item: dict) -> tuple[int, str, dict[str, float]]:
    fid = int(item["fixture"]["id"])
    books = item.get("bookmakers") or []
    bet365 = [b for b in books if int(b.get("id", 0)) == 8]

    def extract(book) -> dict[str, float]:
        out = {}
        for bet in book.get("bets", []):
            mapping = _BETS.get(bet.get("name"))
            if not mapping:
                continue
            for v in bet.get("values", []):
                market = mapping.get(str(v.get("value")))
                if market:
                    try:
                        out[market] = float(v["odd"])
                    except (TypeError, ValueError):
                        pass
        return out

    if bet365:
        return fid, "B365", extract(bet365[0])
    per_book = [extract(b) for b in books]
    markets = {m for d in per_book for m in d}
    avg = {m: round(mean(d[m] for d in per_book if m in d), 3) for m in markets}
    return fid, "AVG", avg


def parse_api_injury(item: dict) -> dict:
    return {
        "api_fixture_id": int(item["fixture"]["id"]),
        "team_api_id": int(item["team"]["id"]),
        "team_name": item["team"]["name"],
        "player_id": int(item["player"]["id"]),
        "player_name": item["player"]["name"],
        "reason": item["player"].get("reason") or item["player"].get("type"),
    }


def parse_api_player(item: dict, team_api_id: int | None = None) -> dict | None:
    stats = item.get("statistics") or []
    if team_api_id is not None:
        stats = [s for s in stats if int((s.get("team") or {}).get("id") or 0) == team_api_id] or stats
    if not stats:
        return None
    minutes = sum(int((s.get("games") or {}).get("minutes") or 0) for s in stats)
    goals = sum(int((s.get("goals") or {}).get("total") or 0) for s in stats)
    assists = sum(int((s.get("goals") or {}).get("assists") or 0) for s in stats)
    pos = POSITIONS.get((stats[0].get("games") or {}).get("position") or "", "M")
    return {"position": pos, "minutes": minutes, "goals": goals, "assists": assists}
