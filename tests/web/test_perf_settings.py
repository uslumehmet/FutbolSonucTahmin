from app.config import api_key, get_settings
from app.models import Match, Team


def test_performance_empty_state(client):
    r = client.get("/performans")
    assert r.status_code == 200 and "Henüz sonuçlanmış kupon yok" in r.text


def test_performance_shows_backtest(client, session_factory):
    from app.models import BacktestRun
    with session_factory() as s:
        s.add(BacktestRun(report={"seasons": [2025], "n_matches": 100, "groups": {"1X2": {"model_ll": 0.98, "base_ll": 1.07,
              "ece": 0.01, "accuracy": 0.53, "calibration": [[0.5, 0.5, 100]]}}, "layer2": {}, "confidence": [
              {"min_prob": 0.7, "n": 10, "avg_prob": 0.75, "hit": 0.7}], "notes": ["Geçmiş bahis oranı yok"]}))
        s.commit()
    r = client.get("/performans")
    assert "Geçmiş test" in r.text and "%53" in r.text and "Geçmiş bahis oranı yok" in r.text


def test_settings_update_thresholds(client, session_factory):
    r = client.post("/ayarlar", data={"safe_min_odds": "1.3", "value_min_prob": "0.45", "value_min_edge": "0.04",
                                      "odds_adjust_pct": "-8", "autoplay_targets": "2, 4", "coupon_max_picks": "5"},
                    follow_redirects=True)
    assert r.status_code == 200 and "Ayarlar kaydedildi" in r.text
    with session_factory() as s:
        st = get_settings(s)
        assert st.safe_min_odds == 1.3 and st.odds_adjust_pct == -8 and st.autoplay_targets == (2.0, 4.0)
        assert st.coupon_max_picks == 5


def test_settings_invalid_value(client):
    r = client.post("/ayarlar", data={"safe_min_odds": "abc"}, follow_redirects=True)
    assert "geçersiz" in r.text.lower()


def test_settings_saves_api_key(client, session_factory):
    client.post("/ayarlar/anahtar", data={"api_key": "  ABC123  "}, follow_redirects=True)
    with session_factory() as s:
        assert api_key(s) == "ABC123"


def test_settings_shows_quota(client):
    assert "/ 100" in client.get("/ayarlar").text


def test_merge_from_settings(client_seeded, session_factory):
    mid = client_seeded.match_ids[0]
    with session_factory() as s:
        m = s.get(Match, mid)
        canon = m.away_team_id
        temp = Team(name="Takim Gecici", kind="club", is_temp=True)
        s.add(temp)
        s.commit()
        m.away_team_id, m.unmatched = temp.id, True
        s.commit()
        temp_id = temp.id
    r = client_seeded.get("/ayarlar")
    assert "Takim Gecici" in r.text
    client_seeded.post("/ayarlar/eslestir", data={"temp_id": temp_id, "hedef": str(canon)}, follow_redirects=True)
    with session_factory() as s:
        assert s.get(Match, mid).away_team_id == canon and s.get(Match, mid).unmatched is False


def test_settings_low_data_toggle(client, session_factory):
    assert 'name="safe_skip_low_data"' in client.get("/ayarlar").text
    client.post("/ayarlar", data={"safe_skip_low_data_sent": "1"}, follow_redirects=True)  # kutu boş
    with session_factory() as s:
        assert get_settings(s).safe_skip_low_data is False
    client.post("/ayarlar", data={"safe_skip_low_data_sent": "1", "safe_skip_low_data": "1"}, follow_redirects=True)
    with session_factory() as s:
        assert get_settings(s).safe_skip_low_data is True
    client.post("/ayarlar", data={"safe_min_odds": "1.25"}, follow_redirects=True)  # başka form: dokunma
    with session_factory() as s:
        assert get_settings(s).safe_skip_low_data is True
