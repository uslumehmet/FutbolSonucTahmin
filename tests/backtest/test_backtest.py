from datetime import timedelta

import pandas as pd
import pytest

from app.backtest import walkforward as wf
from app.backtest.simulate import simulate
from app.backtest.walkforward import train_layer2_all, training_frame, walkforward_club
from app.models import BacktestRun, Match, Prediction
from tests.helpers import seed_synthetic_league


def _seed(session):
    seed_synthetic_league(session, comp_code="E0", seed=1, n_seasons=3, upcoming=0)
    seed_synthetic_league(session, comp_code="SP1", seed=2, n_seasons=3, upcoming=0)


def test_walkforward_no_leakage(session, monkeypatch):
    _seed(session)
    calls = []
    real = wf.fit_dixon_coles

    def spy(df, ref_time, **kw):
        calls.append(ref_time)
        return real(df, ref_time, **kw)

    monkeypatch.setattr(wf, "fit_dixon_coles", spy)
    n = walkforward_club(session, "E0", 2025, 2026)
    assert n > 0
    preds = session.query(Prediction).filter_by(kind="walkforward").all()
    assert len(preds) == n
    for p in preds:
        m = session.get(Match, p.match_id)
        assert p.created_at <= m.kickoff_utc
        assert p.created_at in calls
        assert sum(p.probs[k] for k in ("MS1", "MSX", "MS2")) == pytest.approx(1.0)


def test_walkforward_resumable(session):
    _seed(session)
    n1 = walkforward_club(session, "E0", 2025, 2026)
    n2 = walkforward_club(session, "E0", 2025, 2026)
    assert n1 > 0 and n2 == 0
    assert session.query(Prediction).filter_by(kind="walkforward").count() == n1


def test_training_frame_columns(session):
    _seed(session)
    walkforward_club(session, "E0", 2025, 2026)
    df = training_frame(session, ["E0"])
    assert {"MS1", "UST25", "KGVAR", "home_goals", "away_goals", "kickoff_utc", "h_pts5"} <= set(df.columns)
    assert df["kickoff_utc"].is_monotonic_increasing


def test_train_layer2_all_records_state(session, tmp_path):
    _seed(session)
    for code in ("E0", "SP1"):
        walkforward_club(session, code, 2025, 2026)
    metrics = train_layer2_all(session, path=tmp_path / "l2.pkl")
    assert set(metrics) == {"1X2", "OU25", "BTTS"}
    from app.config import get_value
    assert set(get_value(session, "layer2_active")) == {"1X2", "OU25", "BTTS"}


def test_simulate_report(session):
    _seed(session)
    for code in ("E0", "SP1"):
        walkforward_club(session, code, 2024, 2026)
    rep = simulate(session, seasons=[2026], comp_codes=["E0", "SP1"], train_layer2=False)
    g = rep["groups"]["1X2"]
    assert rep["n_matches"] > 0
    assert 0 <= g["accuracy"] <= 1 and g["model_ll"] > 0 and g["base_ll"] > 0
    assert sum(b[2] for b in g["calibration"]) == rep["n_matches"]
    assert rep["confidence"] and all(0 <= r["hit"] <= 1 for r in rep["confidence"] if r["n"])
    assert any("oran" in n for n in rep["notes"])
    assert session.query(BacktestRun).count() == 1


def test_layer2_trained_only_on_past_seasons(session, monkeypatch):
    _seed(session)
    for code in ("E0", "SP1"):
        walkforward_club(session, code, 2024, 2026)
    seen = []
    from app.backtest import simulate as sim

    def spy(features, l1, y, group):
        seen.append(features.attrs.get("max_kickoff"))
        return None, {"l1_ll": 1.0, "l2_ll": 1.0, "kept": False}

    monkeypatch.setattr(sim, "evaluate_and_train", spy)
    rep = simulate(session, seasons=[2026], comp_codes=["E0", "SP1"], train_layer2=True)
    first_2026 = min(m.kickoff_utc for m in session.query(Match).filter_by(season=2026))
    assert seen and all(pd.Timestamp(k) < pd.Timestamp(first_2026) for k in seen)
    assert set(rep["layer2"]) == {"1X2", "OU25", "BTTS"}
