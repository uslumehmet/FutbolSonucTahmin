from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.data.teams import get_or_create_team
from app.model.features import FEATURE_COLUMNS, build_features, head_to_head, recent_matches
from app.models import Competition, Match

T0 = datetime(2025, 8, 1, 15, tzinfo=UTC)


def _frame(results):
    """results: list of (home, away, hg, ag) one per week."""
    rows = []
    for i, (h, a, hg, ag) in enumerate(results):
        rows.append({"id": i + 1, "kickoff_utc": T0 + timedelta(days=7 * i), "home_id": h, "away_id": a,
                     "home_goals": hg, "away_goals": ag, "elo_home": 1500.0 + i, "elo_away": 1500.0})
    return pd.DataFrame(rows)


def test_no_shots_features():
    assert not any("shot" in c or "sot" in c for c in FEATURE_COLUMNS)


def test_features_use_only_past():
    res = [(1, 2, 2, 0), (3, 1, 0, 1), (1, 4, 1, 1), (5, 1, 2, 3), (1, 6, 0, 2), (1, 2, np.nan, np.nan)]
    base = build_features(_frame(res))
    changed = _frame(res)
    changed.loc[changed.id == 3, ["home_goals", "away_goals"]] = [5, 0]
    f2 = build_features(changed)
    assert base.loc[3].equals(f2.loc[3])
    assert not base.loc[4].equals(f2.loc[4])


def test_pts5_values():
    # takım 1: G, G, B, M, G sonra maç 6
    res = [(1, 2, 2, 0), (3, 1, 0, 1), (1, 4, 1, 1), (5, 1, 3, 0), (1, 6, 2, 1), (1, 7, np.nan, np.nan)]
    f = build_features(_frame(res))
    assert f.loc[6, "h_pts5"] == 2.0
    assert f.loc[6, "h_home_pts5"] == (3 + 1 + 3) / 3
    assert f.loc[6, "elo_diff"] == 5.0
    assert np.isnan(f.loc[1, "h_pts5"])


def test_rest_days_capped():
    res = [(1, 2, 1, 0), (1, 3, np.nan, np.nan)]
    df = _frame(res)
    df.loc[1, "kickoff_utc"] = T0 + timedelta(days=40)
    f = build_features(df)
    assert f.loc[2, "h_rest"] == 14


def test_head_to_head_excludes_future(session):
    comp = session.query(Competition).filter_by(code="T1").one()
    a = get_or_create_team(session, "of", "Galatasaray", "club")
    b = get_or_create_team(session, "of", "Fenerbahce", "club")
    for i, (h, aw, hg, ag) in enumerate([(a, b, 2, 1), (b, a, 0, 0), (a, b, 3, 3)]):
        session.add(Match(competition_id=comp.id, season=2025, kickoff_utc=T0 + timedelta(days=30 * i),
                          home_team_id=h.id, away_team_id=aw.id, status="finished", home_goals=hg, away_goals=ag, stats={}))
    session.commit()
    h2h = head_to_head(session, a.id, b.id, before=T0 + timedelta(days=45))
    assert [(r.gf, r.ga, r.result) for r in h2h] == [(0, 0, "B"), (2, 1, "G")]
    rec = recent_matches(session, a.id, before=T0 + timedelta(days=100), n=10, venue="E")
    assert [r.venue for r in rec] == ["E", "E"]


def test_extra_features_are_leak_free_and_track_core_lineup():
    import numpy as np
    import pandas as pd

    from app.model.features import extra_features
    rows = []
    xi_a = [[str(i), f"P{i}", "M"] for i in range(11)]
    for k in range(7):
        xi = xi_a if k < 6 else [[str(i), f"P{i}", "M"] for i in range(3, 14)]  # son maçta 3 değişiklik
        rows.append({"id": k, "kickoff_utc": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=7 * k),
                     "home_id": 1, "away_id": 2, "home_goals": 1, "away_goals": 0,
                     "stats": {"sot": [4 + k, 2], "shots": [10, 5], "xi_home": xi, "xi_away": []}})
    f = extra_features(pd.DataFrame(rows))
    assert np.isnan(f.loc[0, "h_sot5"])  # ilk maçta geçmiş yok
    assert f.loc[1, "h_sot5"] == 4 and f.loc[1, "h_sota5"] == 2  # sadece önceki maç
    assert f.loc[6, "h_sot5"] == np.mean([5, 6, 7, 8, 9])  # kendi maçı (10) dahil değil
    assert f.loc[1, "a_sot5"] == 2 and f.loc[1, "a_sota5"] == 4
    assert np.isnan(f.loc[4, "h_xi_core"]) and f.loc[5, "h_xi_core"] == 1.0  # en az 5 ilk 11 gerekir
    assert f.loc[6, "h_xi_core"] == 8 / 11 and f.loc[6, "h_xi_changes"] == 3
