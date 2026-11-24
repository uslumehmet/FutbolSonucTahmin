"""API-Football verisi gelmeyince sitede görünen uyarılar ve Ayarlar'daki "API verisi yokken otomatik kupon" seçeneği."""
from datetime import timedelta

from app.config import get_settings, set_value
from app.models import Match, OddsQuote
from tests.helpers import NOW

BANNER = "API-Football'dan veri gelmiyor"


def _live(session_factory, ok=True, message=None):
    with session_factory() as s:
        set_value(s, "api_key", "KEY")
        set_value(s, "api_status", {"ok": ok, "at": NOW.isoformat(), "message": message,
                                    "last_ok": (NOW - timedelta(hours=1)).isoformat()})


def _espn_only(session_factory, mid):
    with session_factory() as s:
        s.query(OddsQuote).filter_by(match_id=mid).delete()
        for market, o in {"MS1": 2.1, "MSX": 3.4, "MS2": 3.5, "UST25": 1.85, "ALT25": 1.95}.items():
            s.add(OddsQuote(match_id=mid, bookmaker="DK", market=market, odds=o, fetched_at=NOW, source="espn"))
        s.commit()


def test_banner_says_why_and_what_is_missing_when_api_never_answered(client_seeded):
    text = client_seeded.get("/").text
    assert BANNER in text and "API anahtarı girilmedi" in text
    assert "Karşılıklı Gol" in text and "Çifte Şans" in text and "sakatlık" in text
    assert "daha çok hata yapabilir" in text


def test_banner_names_a_suspended_account(client_seeded, session_factory):
    _live(session_factory, ok=False, message="Your account is suspended, check on https://dashboard.api-football.com.")
    assert "hesap askıda" in client_seeded.get("/kuponlarim").text


def test_no_banner_while_api_answers(client_seeded, session_factory):
    _live(session_factory)
    assert BANNER not in client_seeded.get("/").text


def test_match_with_only_espn_odds_is_flagged(client_seeded, session_factory):
    _espn_only(session_factory, client_seeded.match_ids[0])
    text = client_seeded.get("/?gun=3gun").text
    assert text.count(">API verisi yok<") == 1  # diğer maçların oranı API'den


def test_espn_odds_while_api_answers_say_api_odds_come_later(client_seeded, session_factory):
    _live(session_factory)
    _espn_only(session_factory, client_seeded.match_ids[0])
    text = client_seeded.get("/?gun=3gun").text
    assert ">API verisi yok<" not in text and text.count(">oran 1 gün önce gelir<") == 1


def test_match_page_explains_missing_double_chance_and_btts(client_seeded, session_factory):
    mid = client_seeded.match_ids[0]
    _espn_only(session_factory, mid)
    text = client_seeded.get(f"/mac/{mid}").text
    assert text.count("Oran yok: API-Football'dan veri gelmedi") >= 2  # Çifte şans ve Karşılıklı gol
    assert "DraftKings (ESPN)" in text


def test_settings_toggle_for_autoplay_without_api(client_seeded, session_factory):
    page = client_seeded.get("/ayarlar").text
    assert 'name="autoplay_without_api"' in page
    client_seeded.post("/ayarlar", data={"autoplay_without_api_sent": "1", "autoplay_without_api": "1"})
    with session_factory() as s:
        assert get_settings(s).autoplay_without_api is True
    client_seeded.post("/ayarlar", data={"autoplay_without_api_sent": "1"})
    with session_factory() as s:
        assert get_settings(s).autoplay_without_api is False


def test_probe_is_refused_on_a_shared_ip_server(client_seeded, session_factory, monkeypatch):
    from app.web import routes_info

    called = []
    monkeypatch.setattr(routes_info, "probe", lambda api, today: called.append(1) or {})
    monkeypatch.setenv("RENDER", "true")
    with session_factory() as s:
        set_value(s, "api_key", "KEY")
    r = client_seeded.post("/ayarlar/probe", follow_redirects=True)
    assert called == [] and "paylaşılan IP" in r.text


def test_settings_show_api_status(client_seeded, session_factory):
    _live(session_factory, ok=False, message="Your account is suspended")
    text = client_seeded.get("/ayarlar").text
    assert "Your account is suspended" in text
