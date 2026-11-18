import pytest

from app.model.injuries import MissingImpact, adjust_lambdas, manual_impact, player_impact


def test_no_missing_unchanged():
    assert adjust_lambdas(1.6, 1.1, [], []) == (1.6, 1.1)


def test_key_striker_reduces_own_lambda():
    imp = player_impact("F", 1800, 16, 0, 20, 40)
    assert imp.attack_effect == pytest.approx(0.14)
    assert adjust_lambdas(2.0, 1.0, [imp], [])[0] == pytest.approx(1.72)


def test_effect_capped():
    imp = player_impact("F", 1800, 16, 0, 20, 40)
    assert adjust_lambdas(2.0, 1.0, [imp] * 3, [])[0] == pytest.approx(1.70)


def test_missing_goalkeeper_raises_opponent():
    gk = player_impact("G", 1800, 0, 0, 20, 30)
    assert gk.concede_effect == pytest.approx(0.06)
    assert adjust_lambdas(1.5, 1.0, [gk], [])[1] == pytest.approx(1.06)


def test_zero_team_games_is_safe():
    assert player_impact("D", 0, 0, 0, 0, 0) == MissingImpact(0.0, 0.0)


def test_manual_levels():
    assert manual_impact("hücum", "yüksek") == MissingImpact(0.10, 0.0)
    assert manual_impact("kaleci", "orta") == MissingImpact(0.0, 0.06)
    assert manual_impact("orta saha", "düşük") == MissingImpact(0.01, 0.005)
