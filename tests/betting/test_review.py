from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.betting.review import condition, review_pick

PRED = SimpleNamespace(lam_home=0.69, lam_away=2.10,
                       probs={"MS1": 0.10, "MSX": 0.20, "MS2": 0.70, "KGVAR": 0.40, "KGYOK": 0.60},
                       top_scores=[[0, 2, 0.136], [0, 1, 0.129], [0, 3, 0.095]])


def _rev(market, hg, ag, result, prob=0.69, odds=1.57, status="finished", pred=PRED):
    return review_pick(market, prob, odds, result, status, hg, ag, "Wales", "Portugal", pred)


def test_condition_texts():
    assert condition("ALT35", "Wales", "Portugal") == "maçta toplam en fazla 3 gol olmalı"
    assert condition("UST25", "Wales", "Portugal") == "maçta toplam en az 3 gol olmalı"
    assert condition("EVALT15", "Wales", "Portugal") == "Wales en fazla 1 gol atmalı"
    assert condition("EVALT05", "Wales", "Portugal") == "Wales gol atmamalı"
    assert condition("DEPUST05", "Wales", "Portugal") == "Portugal en az 1 gol atmalı"
    assert condition("MS2", "Wales", "Portugal") == "Portugal kazanmalı"
    assert condition("CS1X", "Wales", "Portugal") == "Wales kaybetmemeli (kazanır ya da berabere)"
    assert condition("KGYOK", "Wales", "Portugal") == "takımlardan en az biri gol atmamalı"


def test_lost_total_under_explains_more_goals_than_expected():
    r = _rev("ALT35", 2, 3, "lost")
    assert r.happened == "Maç 2–3 bitti; toplam 5 gol oldu."
    assert "toplam 2.8 gol bekliyordu, 5 gol oldu" in r.why and "Beklenenden çok gol oldu" in r.why
    assert any("10 seçimden yaklaşık 3 tanesi tutmaz" in x for x in r.lines)
    assert any("1.57 oranı yaklaşık %64" in x for x in r.lines)
    assert any(x.startswith("Gol beklentisi: Wales 0.69 – 2.10 Portugal") for x in r.lines)
    assert any("0–2 (%14)" in x for x in r.lines)


def test_lost_team_goals_under():
    r = _rev("EVALT15", 2, 3, "lost", prob=0.85, odds=1.22)
    assert r.happened == "Maç 2–3 bitti; Wales 2 gol attı."
    assert "Wales için 0.7 gol bekliyordu, 2 attı" in r.why and "beklenenden fazla" in r.why


def test_lost_match_result_gives_model_chance_of_actual_result():
    r = _rev("MS2", 1, 1, "lost", prob=0.70, odds=1.40)
    assert r.happened == "Maç 1–1 bitti; berabere bitti."
    assert "beraberlik" in r.why and "%20" in r.why


def test_lost_kg():
    r = _rev("KGVAR", 0, 2, "lost", prob=0.40, odds=2.0)
    assert r.happened == "Maç 0–2 bitti; Wales gol atamadı."


def test_won_has_no_failure_chance_line():
    r = _rev("UST15", 2, 3, "won", prob=0.80, odds=1.20)
    assert r.why and "Beklenenden" not in r.why
    assert not any("tutmaz" in x for x in r.lines)


def test_pending_and_void_and_missing_prediction():
    r = _rev("ALT35", None, None, "pending", status="scheduled")
    assert r.happened is None and r.why is None and r.lines  # maç öncesi beklenti yine görünür
    v = _rev("ALT35", None, None, "void", status="postponed")
    assert "iptal" in v.happened
    n = _rev("ALT35", 2, 3, "lost", pred=None)
    assert n.happened and n.why is None and any("%64" in x for x in n.lines)


def test_reviews_for_uses_prediction_made_before_coupon(session_factory):
    from app.betting.review import reviews_for
    from app.models import Competition, Coupon, CouponPick, Match, Prediction, Team

    t0 = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    with session_factory() as s:
        comp = Competition(code="XT", name="Test", country="Test", kind="club")
        h, a = Team(name="Wales", kind="club"), Team(name="Portugal", kind="club")
        s.add_all([comp, h, a])
        s.flush()
        m = Match(competition_id=comp.id, season=2026, kickoff_utc=t0 + timedelta(hours=8), home_team_id=h.id,
                  away_team_id=a.id, status="finished", home_goals=2, away_goals=3)
        s.add(m)
        s.flush()
        s.add_all([Prediction(match_id=m.id, kind="live", created_at=t0 - timedelta(hours=2), lam_home=0.7,
                              lam_away=2.1, probs={}, top_scores=[]),
                   Prediction(match_id=m.id, kind="live", created_at=t0 + timedelta(hours=1), lam_home=1.5,
                              lam_away=1.5, probs={}, top_scores=[])])
        c = Coupon(mode="safe", source="auto", target_odds=2, total_odds=1.57, prob=0.69, status="lost",
                   play_date=t0.date(), created_at=t0)
        s.add(c)
        s.flush()
        p = CouponPick(coupon_id=c.id, match_id=m.id, market="ALT35", odds=1.57, prob=0.69, result="lost")
        s.add(p)
        s.commit()
        out = reviews_for(s, [p])
        assert "toplam 2.8 gol bekliyordu" in out[p.id].why  # kupondan sonraki tahmin (3.0) değil
