from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.data.teams import get_or_create_team
from app.model.markets import MARKET_GROUP
from app.model.predict import latest_prediction, predict_match, predict_upcoming
from app.model.store import ModelStore
from app.models import Competition, Match, MissingPlayer, Prediction
from tests.helpers import NOW, seed_synthetic_league


def test_unmatched_team_gets_no_prediction(session):
    ids = seed_synthetic_league(session, upcoming=1)
    m = session.get(Match, ids[0])
    m.unmatched = True
    session.commit()
    p = predict_match(session, ModelStore(session), m, NOW)
    assert p.probs == {} and "eşleşmedi" in p.flags


def test_prediction_groups_sum_to_one_and_stored(session):
    seed_synthetic_league(session, upcoming=3)
    preds = predict_upcoming(session, ModelStore(session), NOW, days=3)
    assert len(preds) == 3
    for p in preds:
        for group in ("1X2", "OU25", "BTTS"):
            total = sum(v for k, v in p.probs.items() if MARKET_GROUP[k] == group)
            assert total == pytest.approx(1.0)
        assert len(p.top_scores) == 5
    assert session.query(Prediction).filter_by(kind="live").count() == 3
    assert latest_prediction(session, preds[0].match_id).probs == preds[0].probs


def test_missing_striker_lowers_home_win(session):
    ids = seed_synthetic_league(session, upcoming=1)
    m = session.get(Match, ids[0])
    store = ModelStore(session)
    before = predict_match(session, store, m, NOW).probs["MS1"]
    session.add(MissingPlayer(match_id=m.id, team_id=m.home_team_id, player_name="Golcü", source="manual",
                              role="hücum", level="yüksek", attack_effect=0.10, concede_effect=0.0, active=True))
    session.commit()
    after = predict_match(session, store, m, NOW).probs["MS1"]
    assert after < before


class _Fake1X2:
    group = "1X2"

    def predict(self, features, l1):
        return np.array([[0.6, 0.2, 0.2]])


def test_layer2_overrides_only_active_groups(session, monkeypatch):
    ids = seed_synthetic_league(session, upcoming=1)
    m = session.get(Match, ids[0])
    store = ModelStore(session)
    plain = predict_match(session, store, m, NOW)
    monkeypatch.setattr(store, "layer2", lambda: {"1X2": _Fake1X2(), "OU25": None, "BTTS": None})
    p = predict_match(session, store, m, NOW)
    assert p.probs["MS1"] == pytest.approx(0.6) and p.probs["CS1X"] == pytest.approx(0.8)
    assert p.probs["UST25"] == pytest.approx(plain.probs["UST25"])
    assert p.layers == {"1X2": 2, "OU25": 1, "BTTS": 1}


def test_national_match_uses_elo_model(session):
    hist = session.query(Competition).filter_by(code="INTL_HIST").one()
    unl = session.query(Competition).filter_by(code="UNL").one()
    teams = [get_or_create_team(session, "intl", n, "national") for n in ("Strong", "Weak", "C", "D", "E", "F")]
    rng = np.random.default_rng(0)
    strength = [0.8, -0.8, 0.2, 0.0, -0.1, -0.2]
    t = datetime(2016, 1, 1, 12, tzinfo=UTC)
    for _ in range(400):
        i, j = rng.choice(6, 2, replace=False)
        session.add(Match(competition_id=hist.id, season=t.year, kickoff_utc=t, home_team_id=teams[i].id,
                          away_team_id=teams[j].id, status="finished", neutral=False,
                          home_goals=int(rng.poisson(np.exp(0.2 + strength[i] - strength[j] * 0.5))),
                          away_goals=int(rng.poisson(np.exp(0.0 + strength[j] - strength[i] * 0.5))),
                          stats={"tier": "WCQ"}))
        t += timedelta(days=7)
    up = Match(competition_id=unl.id, season=2026, kickoff_utc=NOW + timedelta(days=1), home_team_id=teams[0].id,
               away_team_id=teams[1].id, status="scheduled", stats={})
    session.add(up)
    session.commit()
    p = predict_match(session, ModelStore(session), up, NOW)
    assert p.lam_home > p.lam_away and p.probs["MS1"] > p.probs["MS2"]
