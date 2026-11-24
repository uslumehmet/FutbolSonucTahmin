from datetime import timedelta

import pytest

from app.betting.selection import (
    Candidate, adjust_odds, candidates_for, eligible, implied_probs, latest_odds, match_candidates, safe_pick,
    value_pick,
)
from app.config import DEFAULT_SETTINGS
from app.model.predict import predict_upcoming
from app.model.store import ModelStore
from app.models import Match, OddsQuote
from tests.helpers import NOW, seed_synthetic_league


def test_value():
    assert Candidate(1, "MS1", 2.10, 0.55).value == pytest.approx(0.155)


def test_safe_pick_respects_min_odds():
    c = [Candidate(1, "CS1X", 1.10, 0.85), Candidate(1, "MS1", 1.35, 0.74), Candidate(1, "UST25", 1.55, 0.66)]
    assert safe_pick(c, 1.20).market == "MS1"
    assert safe_pick([Candidate(1, "CS1X", 1.10, 0.85)], 1.20) is None


def test_value_pick_threshold():
    c = [Candidate(1, "MS2", 3.2, 0.38), Candidate(1, "MS1", 2.10, 0.55)]
    assert value_pick(c, 0.40, 0.05).market == "MS1"
    assert value_pick([Candidate(1, "MS1", 1.5, 0.6)], 0.40, 0.05) is None


def test_eligible_modes():
    c = [Candidate(1, "CS1X", 1.10, 0.85), Candidate(1, "MS1", 2.10, 0.55), Candidate(1, "MS2", 3.2, 0.38)]
    assert {x.market for x in eligible(c, "safe", DEFAULT_SETTINGS)} == {"MS1", "MS2"}
    assert {x.market for x in eligible(c, "value", DEFAULT_SETTINGS)} == {"MS1"}


def test_adjust_odds():
    assert adjust_odds(1.85, -8) == 1.70
    assert adjust_odds(1.02, -8) == 1.01
    assert adjust_odds(2.0, 0) == 2.0


def test_implied_probs_removes_margin():
    p = implied_probs({"MS1": 2.0, "MSX": 3.4, "MS2": 3.8, "UST25": 1.9, "ALT25": 1.9})
    assert p["MS1"] + p["MSX"] + p["MS2"] == pytest.approx(1.0)
    assert p["UST25"] == pytest.approx(0.5)


def test_candidates_for_applies_adjustment():
    c = candidates_for(7, {"MS1": 0.5, "MS2": 0.2}, {"MS1": 2.0}, odds_adjust_pct=-10)
    assert c == [Candidate(7, "MS1", 1.8, 0.5, implied=0.5)]


def test_latest_odds_prefers_newest_and_b365(session):
    ids = seed_synthetic_league(session, upcoming=1)
    session.add(OddsQuote(match_id=ids[0], bookmaker="B365", market="MS1", odds=2.5, fetched_at=NOW, source="api"))
    session.commit()
    book, odds = latest_odds(session, ids[0])
    assert book == "B365" and odds["MS1"] == 2.5 and odds["MSX"] == 3.4


def test_match_without_odds_not_candidate(session):
    ids = seed_synthetic_league(session, upcoming=2, with_odds=False)
    predict_upcoming(session, ModelStore(session), NOW)
    assert match_candidates(session, NOW, NOW + timedelta(days=3), DEFAULT_SETTINGS) == {}


def test_unmatched_match_not_candidate(session):
    ids = seed_synthetic_league(session, upcoming=2)
    predict_upcoming(session, ModelStore(session), NOW)
    session.get(Match, ids[0]).unmatched = True
    session.commit()
    cands = match_candidates(session, NOW, NOW + timedelta(days=3), DEFAULT_SETTINGS)
    assert set(cands) == {ids[1]}
    assert all(isinstance(c, Candidate) for c in cands[ids[1]])


def test_candidates_carry_market_implied_prob():
    c = candidates_for(3, {"UST25": 0.43, "ALT25": 0.57}, {"UST25": 1.36, "ALT25": 3.10}, odds_adjust_pct=0)
    alt = [x for x in c if x.market == "ALT25"][0]
    assert alt.implied == pytest.approx((1 / 3.10) / (1 / 1.36 + 1 / 3.10))


def test_eligible_drops_picks_where_model_far_above_market():
    big_gap = Candidate(1, "ALT25", 3.10, 0.57, implied=0.30)
    ok = Candidate(2, "MS1", 2.10, 0.55, implied=0.46)
    assert eligible([big_gap, ok], "value", DEFAULT_SETTINGS) == [ok]
    assert eligible([big_gap, ok], "safe", DEFAULT_SETTINGS) == [ok]
    assert DEFAULT_SETTINGS.max_model_gap == 0.15


def test_implied_probs_new_pairs_normalized():
    p = implied_probs({"EVUST15": 2.0, "EVALT15": 1.75, "DEPUST05": 1.5})
    assert p["EVUST15"] + p["EVALT15"] == pytest.approx(1.0)
    assert p["DEPUST05"] == pytest.approx(1 / 1.5)


# --- verisi az maçlar Güvenli moddan çıkar (ayarla kapatılabilir) ---
from dataclasses import replace  # noqa: E402


def test_safe_mode_skips_low_data_matches_by_default():
    rich, poor = Candidate(1, "CS1X", 1.30, 0.80), Candidate(2, "CS1X", 1.30, 0.85, low_data=True)
    assert DEFAULT_SETTINGS.safe_skip_low_data is True
    assert eligible([rich, poor], "safe", DEFAULT_SETTINGS) == [rich]
    off = replace(DEFAULT_SETTINGS, safe_skip_low_data=False)
    assert eligible([rich, poor], "safe", off) == [rich, poor]


def test_value_mode_keeps_low_data_matches():
    poor = Candidate(2, "MS1", 2.50, 0.50, low_data=True)
    assert eligible([poor], "value", DEFAULT_SETTINGS) == [poor]


def test_match_candidates_carry_low_data_flag(session):
    from app.models import Prediction
    ids = seed_synthetic_league(session, upcoming=2)
    predict_upcoming(session, ModelStore(session), NOW)
    pred = session.query(Prediction).filter_by(match_id=ids[0]).order_by(Prediction.id.desc()).first()
    pred.flags = ["veri az"]
    session.commit()
    cands = match_candidates(session, NOW, NOW + timedelta(days=3), DEFAULT_SETTINGS)
    assert all(c.low_data for c in cands[ids[0]]) and not any(c.low_data for c in cands[ids[1]])


def test_sectioned_orders_by_market_sections():
    from app.betting.selection import sectioned

    alt, ms1 = Candidate(1, "ALT25", 1.8, 0.6), Candidate(1, "MS1", 1.5, 0.7)
    assert sectioned([alt, ms1]) == [("Maç sonucu", [ms1]), ("Toplam gol", [alt])]


def test_candidates_for_match_matches_match_candidates(session):
    from app.betting.selection import candidates_for_match

    ids = seed_synthetic_league(session, upcoming=2)
    predict_upcoming(session, ModelStore(session), NOW)
    all_cands = match_candidates(session, NOW, NOW + timedelta(days=3), DEFAULT_SETTINGS)
    m = session.get(Match, ids[0])
    assert candidates_for_match(session, m, DEFAULT_SETTINGS) == all_cands[m.id]
    session.query(OddsQuote).filter_by(match_id=m.id).delete()
    session.commit()
    assert candidates_for_match(session, m, DEFAULT_SETTINGS) == []


def _q(session, mid, book, source, when, **odds):
    for market, o in odds.items():
        session.add(OddsQuote(match_id=mid, bookmaker=book, market=market, odds=o, fetched_at=when, source=source))
    session.commit()


def test_fresh_espn_odds_beat_stale_api_odds(session):
    from app.betting.selection import odds_info

    ids = seed_synthetic_league(session, upcoming=1, with_odds=False)
    _q(session, ids[0], "B365", "api", NOW - timedelta(days=3), MS1=2.0, MSX=3.3, MS2=3.9)
    _q(session, ids[0], "DK", "espn", NOW, MS1=2.4, MSX=3.2, MS2=3.1)
    info = odds_info(session, ids[0])
    assert (info.source, info.book, info.odds["MS1"]) == ("espn", "DK", 2.4)


def test_recent_api_odds_beat_espn_odds(session):
    from app.betting.selection import odds_info

    ids = seed_synthetic_league(session, upcoming=1, with_odds=False)
    _q(session, ids[0], "B365", "api", NOW - timedelta(hours=5), MS1=2.0, MSX=3.3, MS2=3.9, KGVAR=1.7)
    _q(session, ids[0], "DK", "espn", NOW, MS1=2.4, MSX=3.2, MS2=3.1)
    info = odds_info(session, ids[0])
    assert (info.source, info.book, info.odds["MS1"], info.odds["KGVAR"]) == ("api", "B365", 2.0, 1.7)


def test_espn_bet365_with_double_chance_preferred_over_draftkings(session):
    from app.betting.selection import odds_info

    ids = seed_synthetic_league(session, upcoming=1, with_odds=False)
    _q(session, ids[0], "DK", "espn", NOW, MS1=2.4, MSX=3.2, MS2=3.1)
    _q(session, ids[0], "B365", "espn", NOW - timedelta(hours=1), MS1=2.3, MSX=3.25, MS2=3.2, CS1X=1.36)
    info = odds_info(session, ids[0])
    assert (info.source, info.book, info.odds["CS1X"]) == ("espn", "B365", 1.36)


def test_espn_bet365_is_completed_with_draftkings_totals(session):
    """ESPN'deki Bet365'te çoğu zaman Alt/Üst yok; o maçta DraftKings'in 2.5 Alt/Üst oranı kaybolmasın."""
    from app.betting.selection import odds_info

    ids = seed_synthetic_league(session, upcoming=1, with_odds=False)
    _q(session, ids[0], "DK", "espn", NOW, MS1=2.4, MSX=3.2, MS2=3.1, UST25=1.9, ALT25=1.9)
    _q(session, ids[0], "B365", "espn", NOW, MS1=2.3, MSX=3.25, MS2=3.2, CS1X=1.36, CS12=1.3, CSX2=1.6)
    info = odds_info(session, ids[0])
    assert info.odds == {"MS1": 2.3, "MSX": 3.25, "MS2": 3.2, "CS1X": 1.36, "CS12": 1.3, "CSX2": 1.6,
                         "UST25": 1.9, "ALT25": 1.9}
