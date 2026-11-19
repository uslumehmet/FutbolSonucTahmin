import math

import pytest

from app.model.markets import market_probs, pick_wins, probs_from_1x2, score_matrix, top_scores


def test_probabilities_sum_to_one():
    p = market_probs(score_matrix(1.5, 1.1))
    assert abs(p["MS1"] + p["MSX"] + p["MS2"] - 1) < 1e-9
    assert abs(p["UST25"] + p["ALT25"] - 1) < 1e-9
    assert abs(p["KGVAR"] + p["KGYOK"] - 1) < 1e-9
    assert p["CS1X"] == pytest.approx(p["MS1"] + p["MSX"])


def test_independent_poisson_values():
    m = score_matrix(1.5, 1.1)
    assert m[0, 0] == pytest.approx(math.exp(-2.6), rel=1e-4)
    assert market_probs(m)["KGVAR"] == pytest.approx((1 - math.exp(-1.5)) * (1 - math.exp(-1.1)), rel=1e-4)


def test_negative_rho_raises_draw_00():
    assert score_matrix(1.2, 1.0, rho=-0.1)[0, 0] > score_matrix(1.2, 1.0)[0, 0]


def test_probs_from_1x2_recomputes_double_chance():
    p = probs_from_1x2(0.5, 0.3, 0.2, market_probs(score_matrix(1.5, 1.1)))
    assert p["CS1X"] == pytest.approx(0.8)
    assert p["CSX2"] == pytest.approx(0.5)
    assert p["CS12"] == pytest.approx(0.7)


def test_top_scores_sorted():
    ts = top_scores(score_matrix(1.5, 1.1), 5)
    assert len(ts) == 5
    assert ts[0][2] >= ts[1][2] >= ts[4][2]
    assert (ts[0][0], ts[0][1]) == (1, 1)


@pytest.mark.parametrize("market,h,a,exp", [
    ("MS1", 2, 1, True), ("MSX", 1, 1, True), ("CS12", 0, 0, False), ("UST25", 2, 1, True),
    ("ALT25", 1, 1, True), ("KGVAR", 1, 0, False), ("KGYOK", 0, 0, True), ("CSX2", 0, 2, True),
])
def test_pick_wins(market, h, a, exp):
    assert pick_wins(market, h, a) is exp


def test_extra_goal_lines_and_team_totals():
    p = market_probs(score_matrix(1.5, 1.1))
    for a, b in (("UST15", "ALT15"), ("UST35", "ALT35"), ("EVUST05", "EVALT05"), ("EVUST15", "EVALT15"),
                 ("DEPUST05", "DEPALT05"), ("DEPUST15", "DEPALT15")):
        assert p[a] + p[b] == pytest.approx(1.0)
    assert p["ALT15"] == pytest.approx(math.exp(-2.6) * (1 + 2.6), rel=1e-4)
    assert p["EVUST05"] == pytest.approx(1 - math.exp(-1.5), rel=1e-4)
    assert p["DEPUST15"] == pytest.approx(1 - math.exp(-1.1) * (1 + 1.1), rel=1e-4)
    assert p["UST15"] > p["UST25"] > p["UST35"]


@pytest.mark.parametrize("market,h,a,exp", [
    ("UST15", 1, 1, True), ("ALT15", 1, 0, True), ("UST35", 2, 2, True), ("ALT35", 2, 1, True),
    ("EVUST05", 1, 0, True), ("EVUST15", 1, 3, False), ("DEPUST15", 0, 2, True), ("DEPALT05", 3, 0, True),
    ("EVALT15", 1, 4, True), ("DEPALT15", 2, 2, False),
])
def test_pick_wins_extra_markets(market, h, a, exp):
    assert pick_wins(market, h, a) is exp


def test_labels_and_groups_cover_all_markets():
    from app.model.markets import MARKET_GROUP, MARKET_LABELS, MARKETS
    assert len(MARKETS) == 22 and set(MARKETS) == set(MARKET_LABELS) == set(MARKET_GROUP)
    assert MARKET_LABELS["DEPUST15"] == "Deplasman golleri 1.5 Üst" and MARKET_LABELS["EVUST15"] == "Ev sahibi golleri 1.5 Üst"
    assert MARKET_LABELS["ALT15"] == "Toplam gol 1.5 Alt"


def test_market_label_uses_team_names():
    from app.model.markets import market_label, market_parts
    assert market_parts("EVALT15", "Galler", "Portekiz") == ("Ev sahibi 1.5 Alt", "Galler golleri")
    assert market_parts("DEPUST05", "Galler", "Portekiz") == ("Deplasman 0.5 Üst", "Portekiz golleri")
    assert market_parts("ALT35", "Galler", "Portekiz") == ("Toplam gol 3.5 Alt", None)
    assert market_parts("MS2", "Galler", "Portekiz") == ("MS 2", "Portekiz kazanır")
    assert market_label("EVALT15", "Galler", "Portekiz") == "Ev sahibi 1.5 Alt (Galler golleri)"
    assert market_label("ALT35", "Galler", "Portekiz") == "Toplam gol 3.5 Alt"
    assert market_label("MS2", "Galler", "Portekiz") == "MS 2 (Portekiz kazanır)"
    # takım adı yoksa kısa ad kullanılmaz; "Ev sahibi 1.5 Alt" tek başına toplam golle karışır
    assert market_label("EVALT15") == "Ev sahibi golleri 1.5 Alt"
