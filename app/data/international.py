"""martj42/international_results milli maç verisi."""
from __future__ import annotations

import io

import pandas as pd

INTL_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
MIN_DATE = "1990-01-01"

_CONTINENTAL = {"Copa América", "African Cup of Nations", "AFC Asian Cup", "Gold Cup", "Oceania Nations Cup"}


def tournament_tier(name: str) -> str:
    name = name.strip()
    if name == "FIFA World Cup":
        return "WC"
    if name == "UEFA Euro":
        return "EURO"
    if name in ("UEFA Nations League", "CONCACAF Nations League"):
        return "UNL"
    if name == "FIFA World Cup qualification":
        return "WCQ"
    if name == "UEFA Euro qualification":
        return "EUROQ"
    if name.endswith(" qualification"):
        return "QUAL"
    if name in _CONTINENTAL:
        return "CONT"
    if name == "Friendly":
        return "FRIENDLY"
    return "OTHER"


def parse_international(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df = df[df["date"] >= MIN_DATE]
    df = df[df["home_score"].notna() & df["away_score"].notna()]
    df = df[df["home_score"].str.fullmatch(r"\d+") & df["away_score"].str.fullmatch(r"\d+")]
    out = pd.DataFrame({
        "kickoff_utc": pd.to_datetime(df["date"] + " 12:00", format="%Y-%m-%d %H:%M").dt.tz_localize("UTC"),
        "home": df["home_team"].str.strip(),
        "away": df["away_team"].str.strip(),
        "home_score": df["home_score"].astype(int),
        "away_score": df["away_score"].astype(int),
        "tournament": df["tournament"].str.strip(),
        "neutral": df["neutral"].str.upper().eq("TRUE"),
    })
    out["tier"] = out["tournament"].map(tournament_tier)
    return out.sort_values("kickoff_utc").reset_index(drop=True)
