from datetime import date

import pytest

from app.betting.stats import live_calibration, mode_stats, singles_stats
from app.models import Coupon, Match, Prediction, SinglePick
from tests.helpers import NOW, seed_synthetic_league


def _coupon(session, status, odds, mode="safe", target=3.0):
    c = Coupon(mode=mode, source="auto", target_odds=target, total_odds=odds, prob=0.3, status=status,
               settled_odds=odds if status == "won" else 0.0, play_date=date(2026, 11, 16))
    session.add(c)
    session.commit()


def test_mode_stats_unit_roi(session):
    _coupon(session, "won", 3.0)
    _coupon(session, "lost", 3.2)
    _coupon(session, "lost", 2.9)
    _coupon(session, "open", 3.1)
    _coupon(session, "won", 2.0, mode="value", target=2.0)
    st = mode_stats(session, "safe")
    assert st["coupons"] == 3 and st["won"] == 1 and st["open"] == 1
    assert st["unit_roi"] == pytest.approx(0.0)
    assert st["by_target"][3.0]["coupons"] == 3
    assert mode_stats(session, "value")["unit_roi"] == pytest.approx(1.0)


def test_user_coupons_excluded_from_mode_stats(session):
    session.add(Coupon(mode="safe", source="user", target_odds=3, total_odds=3, prob=0.3, status="won",
                       settled_odds=3, play_date=date(2026, 11, 16)))
    session.commit()
    assert mode_stats(session, "safe")["coupons"] == 0


def test_singles_stats_by_market(session):
    ids = seed_synthetic_league(session, upcoming=3)
    for mid, market, res, odds in [(ids[0], "MS1", "won", 2.0), (ids[1], "MS1", "lost", 2.0), (ids[2], "UST25", "won", 1.8)]:
        session.add(SinglePick(mode="value", match_id=mid, market=market, odds=odds, prob=0.5,
                               play_date=date(2026, 11, 16), result=res))
    session.commit()
    total = singles_stats(session, "value")[0]
    assert total["n"] == 3 and total["won"] == 2 and total["unit_roi"] == pytest.approx((2.0 + 1.8 - 3) / 3)
    by = {r["key"]: r for r in singles_stats(session, "value", by="market")}
    assert by["MS 1"]["n"] == 2 and by["Toplam gol 2.5 Üst"]["won"] == 1


def test_live_calibration(session):
    ids = seed_synthetic_league(session, upcoming=2)
    for mid, p, hg in [(ids[0], 0.72, 2), (ids[1], 0.75, 0)]:
        session.add(Prediction(match_id=mid, kind="live", created_at=NOW, probs={"MS1": p, "MSX": 0.15, "MS2": 0.13}))
        m = session.get(Match, mid)
        m.status, m.home_goals, m.away_goals = "finished", hg, 1
    session.commit()
    bins = live_calibration(session, "1X2")
    assert bins == [[pytest.approx(0.735), 0.5, 2]]


def test_league_accuracy_counts_hits_favorites_and_current_season(session):
    from datetime import UTC, datetime

    from app.betting.stats import league_accuracy
    from app.models import Competition, Team
    comp = session.query(Competition).filter_by(code="T1").one()
    a, b = Team(name="A", kind="club"), Team(name="B", kind="club")
    session.add_all([a, b])
    session.flush()

    def add(season, hg, ag, probs):
        m = Match(competition_id=comp.id, season=season, kickoff_utc=datetime(season, 10, 1, tzinfo=UTC),
                  home_team_id=a.id, away_team_id=b.id, status="finished", home_goals=hg, away_goals=ag, stats={})
        session.add(m)
        session.flush()
        session.add(Prediction(match_id=m.id, kind="walkforward", probs=probs, created_at=m.kickoff_utc))

    fav = {"MS1": 0.65, "MSX": 0.2, "MS2": 0.15, "UST25": 0.6}
    add(2026, 2, 1, fav)          # favori tuttu, 2.5 Üst tuttu, bu sezon
    add(2025, 0, 0, fav)          # favori tutmadı, 2.5 Alt oldu
    add(2025, 1, 2, {"MS1": 0.3, "MSX": 0.3, "MS2": 0.4, "UST25": 0.4})  # doğru (MS2), favori değil
    add(2020, 3, 0, fav)          # eski sezon: sayılmaz
    session.commit()
    out = league_accuracy(session, date(2026, 11, 20))
    t1 = next(x for x in out["leagues"] if x["code"] == "T1")
    assert (t1["n"], t1["ok"], t1["fav_n"], t1["fav_ok"], t1["cur_n"], t1["cur_ok"]) == (3, 2, 2, 1, 1, 1)
    assert (t1["ou_n"], t1["ou_ok"], t1["home"]) == (3, 1, 1)
    assert out["seasons"] == [2024, 2026] and out["total"]["n"] == 3
