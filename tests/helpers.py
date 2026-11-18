"""Testler için sentetik lig üretimi."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd


def simulate_league(seed: int, n_teams: int = 10, n_seasons: int = 2, home_adv: float = 0.25,
                    start: datetime = datetime(2023, 8, 1, 15, tzinfo=UTC), attack=None, defence=None):
    """Bilinen güçlerle çift devreli lig üretir. Döner: (df, gerçek parametreler)."""
    rng = np.random.default_rng(seed)
    attack = np.asarray(attack if attack is not None else np.linspace(-0.4, 0.4, n_teams))
    defence = np.asarray(defence if defence is not None else np.linspace(0.3, -0.3, n_teams))
    attack = attack - attack.mean()
    rows = []
    t = start
    for _season in range(n_seasons):
        for h in range(n_teams):
            for a in range(n_teams):
                if h == a:
                    continue
                lam = np.exp(attack[h] + defence[a] + home_adv)
                mu = np.exp(attack[a] + defence[h])
                rows.append({"home_id": h + 1, "away_id": a + 1, "home_goals": int(rng.poisson(lam)),
                             "away_goals": int(rng.poisson(mu)), "kickoff_utc": t})
                t += timedelta(hours=8)
        t += timedelta(days=60)
    df = pd.DataFrame(rows)
    truth = {"attack": {i + 1: float(attack[i]) for i in range(n_teams)},
             "defence": {i + 1: float(defence[i]) for i in range(n_teams)}, "home_adv": home_adv}
    return df, truth


NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def seed_synthetic_league(session, comp_code: str = "E0", seed: int = 1, n_seasons: int = 3, upcoming: int = 5,
                          n_teams: int = 10, with_odds: bool = True, now: datetime = NOW) -> list[int]:
    """Veritabanına sentetik bir lig ekler; gelecek (scheduled) maçların id'lerini döndürür."""
    from app.data.teams import get_or_create_team
    from app.models import Competition, Match, OddsQuote

    comp = session.query(Competition).filter_by(code=comp_code).one()
    df, _ = simulate_league(seed, n_teams=n_teams, n_seasons=n_seasons,
                            start=now - timedelta(days=95 * n_seasons + 20))
    teams = {i: get_or_create_team(session, "of", f"{comp_code} Takım {i}", "club") for i in range(1, n_teams + 1)}
    season_len = len(df) // n_seasons
    for k, row in enumerate(df.itertuples()):
        session.add(Match(competition_id=comp.id, season=2026 - n_seasons + 1 + k // season_len,
                          kickoff_utc=row.kickoff_utc.to_pydatetime(), home_team_id=teams[row.home_id].id,
                          away_team_id=teams[row.away_id].id, status="finished", home_goals=row.home_goals,
                          away_goals=row.away_goals, stats={}))
    ids = []
    for j in range(upcoming):
        h, a = teams[(2 * j) % n_teams + 1], teams[(2 * j + 1) % n_teams + 1]
        m = Match(competition_id=comp.id, season=2026, kickoff_utc=now + timedelta(hours=26 + 3 * j),
                  home_team_id=h.id, away_team_id=a.id, status="scheduled", stats={})
        session.add(m)
        session.flush()
        ids.append(m.id)
        if with_odds:
            odds = {"MS1": 2.10, "MSX": 3.40, "MS2": 3.50, "CS1X": 1.30, "CS12": 1.33, "CSX2": 1.72,
                    "UST25": 1.85, "ALT25": 1.95, "KGVAR": 1.80, "KGYOK": 2.00}
            for market, o in odds.items():
                session.add(OddsQuote(match_id=m.id, bookmaker="B365", market=market, odds=o,
                                      fetched_at=now - timedelta(hours=1), source="api"))
    session.commit()
    return ids
