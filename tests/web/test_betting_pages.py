import re
from datetime import timedelta

from app.betting.autoplay import run_autoplay
from app.models import Coupon, Match, OddsQuote, Wallet
from app.model.predict import predict_upcoming
from app.model.store import ModelStore
from tests.helpers import NOW


def _make_value_odds(session_factory):
    """Oranları modele göre biraz yüksek yap ki iki modda da kupon kurulabilsin."""
    with session_factory() as s:
        preds = {p.match_id: p for p in predict_upcoming(s, ModelStore(s), NOW)}
        s.query(OddsQuote).delete()
        for mid, p in preds.items():
            for market, prob in p.probs.items():
                s.add(OddsQuote(match_id=mid, bookmaker="B365", market=market, odds=round(1.15 / prob, 2),
                                fetched_at=NOW, source="api"))
        s.commit()


def _user_wallet(session_factory):
    with session_factory() as s:
        return s.query(Wallet).filter_by(owner="user").one()


def _create(client, mode="safe", hedef="3"):
    r = client.post("/kupon/olustur", data={"hedef": hedef, "mod": mode, "gun": "3gun"})
    assert r.status_code == 200
    ids = re.findall(r'action="/kupon/(\d+)/oyna"', r.text)
    return r, [int(x) for x in ids]


def test_coupon_form_page(client_seeded):
    r = client_seeded.get("/kupon")
    assert r.status_code == 200 and "Hedef oran" in r.text and "Güvenli" in r.text and "Değerli" in r.text


def test_create_coupon_and_play(client_seeded, session_factory):
    _make_value_odds(session_factory)
    r, ids = _create(client_seeded)
    assert "Toplam oran" in r.text and "tutma ihtimali" in r.text and ids
    w = _user_wallet(session_factory)
    r = client_seeded.post(f"/kupon/{ids[0]}/oyna", data={"wallet_id": w.id, "tutar": "10"}, follow_redirects=True)
    assert r.status_code == 200
    with session_factory() as s:
        assert s.get(Wallet, w.id).balance == 99_000
        assert s.get(Coupon, ids[0]).status == "open"
    assert "Açık" in r.text


def test_value_mode_without_value_shows_reason(client_seeded):
    r, ids = _create(client_seeded, mode="value", hedef="50")
    assert not ids and "değerli seçim yetersiz" in r.text.lower()


def test_insufficient_balance_message(client_seeded, session_factory):
    _make_value_odds(session_factory)
    _, ids = _create(client_seeded)
    w = _user_wallet(session_factory)
    r = client_seeded.post(f"/kupon/{ids[0]}/oyna", data={"wallet_id": w.id, "tutar": "5000"}, follow_redirects=True)
    assert "Bakiye yetersiz" in r.text
    with session_factory() as s:
        assert s.get(Coupon, ids[0]).status == "draft"


def test_cannot_play_started_match(client_seeded, session_factory):
    _make_value_odds(session_factory)
    _, ids = _create(client_seeded)
    with session_factory() as s:
        for p in s.get(Coupon, ids[0]).picks:
            s.get(Match, p.match_id).kickoff_utc = NOW - timedelta(minutes=5)
        s.commit()
    w = _user_wallet(session_factory)
    r = client_seeded.post(f"/kupon/{ids[0]}/oyna", data={"wallet_id": w.id, "tutar": "10"}, follow_redirects=True)
    assert "Maç başladı" in r.text


def test_kuponlarim_lists_system_coupons(client_seeded, session_factory):
    _make_value_odds(session_factory)
    with session_factory() as s:
        for m in s.query(Match).filter_by(status="scheduled"):
            m.kickoff_utc = NOW + timedelta(hours=3)
        s.commit()
        run_autoplay(s, NOW)
        w = s.query(Wallet).filter_by(name="Sistem · Güvenli 100 ₺").one()
    r = client_seeded.get(f"/kuponlarim?kasa={w.id}")
    assert r.status_code == 200 and "Sistem · Güvenli 100 ₺" in r.text and "Açık" in r.text


def test_create_custom_wallet(client_seeded, session_factory):
    r = client_seeded.post("/kasalar", data={"ad": "Deneme 500", "oynayan": "value", "baslangic": "500",
                                             "kural": "percent", "deger": "3"}, follow_redirects=True)
    assert r.status_code == 200 and "Deneme 500" in r.text
    with session_factory() as s:
        w = s.query(Wallet).filter_by(name="Deneme 500").one()
        assert w.balance == 50_000 and w.owner == "value"


def test_create_wallet_invalid_shows_error(client_seeded):
    r = client_seeded.post("/kasalar", data={"ad": "X", "oynayan": "user", "baslangic": "0", "kural": "fixed",
                                             "deger": "10"}, follow_redirects=True)
    assert "en az 1" in r.text


def test_reset_and_delete_wallet(client_seeded, session_factory):
    w = _user_wallet(session_factory)
    with session_factory() as s:
        s.get(Wallet, w.id).balance = 5
        s.commit()
    client_seeded.post(f"/kasalar/{w.id}/sifirla", follow_redirects=True)
    with session_factory() as s:
        assert s.get(Wallet, w.id).balance == 100_000
    client_seeded.post(f"/kasalar/{w.id}/sil", follow_redirects=True)
    with session_factory() as s:
        assert s.get(Wallet, w.id) is None


def test_league_filter_without_matches_names_available_leagues(client_seeded, session_factory):
    _make_value_odds(session_factory)
    r = client_seeded.post("/kupon/olustur", data={"hedef": "3", "mod": "safe", "gun": "3gun", "lig": ["T1"]})
    assert "Seçtiğin liglerde" in r.text and "Premier League" in r.text


def test_change_wallet_stake_rule(client_seeded, session_factory):
    with session_factory() as s:
        w = s.query(Wallet).filter_by(name="Sistem · Güvenli 1.000 ₺").one()
    r = client_seeded.post(f"/kasalar/{w.id}/kural", data={"kural": "fixed", "deger": "100"}, follow_redirects=True)
    assert r.status_code == 200 and "güncellendi" in r.text
    with session_factory() as s:
        w2 = s.get(Wallet, w.id)
        assert (w2.stake_rule, w2.stake_value) == ("fixed", 100.0)
    r = client_seeded.post(f"/kasalar/{w.id}/kural", data={"kural": "percent", "deger": "0"}, follow_redirects=True)
    assert "Geçersiz" in r.text


def test_coupon_form_lists_only_leagues_with_matches(client_seeded, session_factory):
    _make_value_odds(session_factory)
    r = client_seeded.get("/kupon")
    assert 'type="checkbox" name="lig" value="E0"' in r.text
    assert "Premier League (5 maç)" in r.text
    assert 'value="T1"' not in r.text and "<select name=\"lig\"" not in r.text


def test_rename_wallet(client_seeded, session_factory):
    with session_factory() as s:
        wid = s.query(Wallet).first().id
    r = client_seeded.post(f"/kasalar/{wid}/ad", data={"ad": "  Deneme Kasası  "}, follow_redirects=True)
    assert "Deneme Kasası" in r.text and "adı değişti" in r.text
    with session_factory() as s:
        assert s.get(Wallet, wid).name == "Deneme Kasası"
    r = client_seeded.post(f"/kasalar/{wid}/ad", data={"ad": "   "}, follow_redirects=True)
    assert "boş olamaz" in r.text
    assert f'action="/kasalar/{wid}/ad"' in client_seeded.get("/kasalar").text


def test_kuponlarim_shows_autoplay_status(client_seeded):
    from app.config import set_value
    from tests.helpers import NOW

    with client_seeded.app.state.sf() as s:
        set_value(s, "last_autoplay", {"coupons": 0, "singles": 0, "at": NOW.isoformat()})
        from app.models import Wallet
        wid = s.query(Wallet).filter(Wallet.owner == "safe").first().id
    html = client_seeded.get(f"/kuponlarim?kasa={wid}").text
    assert "uygun maç yoktu" in html and "Sıradaki maçlar" in html


def test_negative_net_shown_red(client_seeded, session_factory):
    from datetime import date

    from app.models import Bet, Coupon
    w = _user_wallet(session_factory)
    with session_factory() as s:
        c = Coupon(mode="safe", source="user", target_odds=2.0, total_odds=2.0, prob=0.5, status="lost",
                   play_date=date(2026, 11, 16))
        s.add(c)
        s.flush()
        s.add(Bet(wallet_id=w.id, coupon_id=c.id, stake=1000, payout=0))
        s.commit()
    kas = client_seeded.get("/kasalar").text
    assert 'class="num loss">' in kas  # eksi net kırmızı
    assert 'class="loss">' in client_seeded.get(f"/kuponlarim?kasa={w.id}").text
