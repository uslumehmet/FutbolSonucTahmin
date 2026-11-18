import re
from pathlib import Path

from app.models import Match, MissingPlayer, Prediction

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "web" / "templates"


def _ms1(html: str) -> int:
    m = re.search(r'data-market="MS1"[^>]*data-prob="(\d+)"', html)
    assert m, "MS1 olasılığı sayfada yok"
    return int(m.group(1))


def test_matches_page_lists_league_and_probs(client_seeded):
    r = client_seeded.get("/?gun=3gun")
    assert r.status_code == 200
    assert "Premier League" in r.text and "%" in r.text
    assert "E0 Takım 1" in r.text


def test_matches_page_empty_day_message(client_seeded):
    r = client_seeded.get("/?gun=bugun")
    assert r.status_code == 200 and "Bu aralıkta maç yok" in r.text


def test_api_key_warning_shown(client_seeded):
    assert "API anahtarı" in client_seeded.get("/?gun=3gun").text


def test_match_detail_shows_markets(client_seeded):
    r = client_seeded.get(f"/mac/{client_seeded.match_ids[0]}")
    assert r.status_code == 200
    for text in ("MS 1", "2.5 Üst", "KG Var", "Gol beklentisi", "Son maçlar", "Transfermarkt"):
        assert text in r.text


def test_match_detail_404(client_seeded):
    assert client_seeded.get("/mac/999999").status_code == 404


def test_add_and_remove_manual_missing_changes_probability(client_seeded, session_factory):
    mid = client_seeded.match_ids[0]
    before = _ms1(client_seeded.get(f"/mac/{mid}").text)
    with session_factory() as s:
        home = s.get(Match, mid).home_team_id
    r = client_seeded.post(f"/mac/{mid}/eksik", data={"team_id": home, "player_name": "Golcü", "role": "hücum",
                                                      "level": "yüksek"}, follow_redirects=True)
    assert r.status_code == 200 and "Golcü" in r.text
    after = _ms1(r.text)
    assert after < before
    with session_factory() as s:
        mp = s.query(MissingPlayer).filter_by(match_id=mid).one()
    r = client_seeded.post(f"/mac/{mid}/eksik/{mp.id}/kaldir", follow_redirects=True)
    assert "Golcü" not in r.text and _ms1(r.text) == before


def test_unmatched_flag_shown(client_seeded, session_factory):
    mid = client_seeded.match_ids[1]
    with session_factory() as s:
        s.get(Match, mid).unmatched = True
        s.query(Prediction).filter_by(match_id=mid).delete()
        s.commit()
    assert "eşleşmedi" in client_seeded.get("/?gun=3gun").text


def test_no_certainty_words_in_templates():
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8").lower()
        for word in ("kesin", "banko", "garanti"):
            assert word not in text, f"{path.name}: {word}"


def test_match_detail_shows_odds_time(client_seeded):
    r = client_seeded.get(f"/mac/{client_seeded.match_ids[0]}")
    assert "Oranların alındığı zaman" in r.text


def test_h2h_shows_real_fixture_score_and_winner(client_seeded):
    r = client_seeded.get(f"/mac/{client_seeded.match_ids[0]}")
    sec = r.text[r.text.index("Aralarındaki maçlar"):]
    assert "Kazanan" in sec
    assert "E0 Takım 2 – E0 Takım 1" in sec or "E0 Takım 1 – E0 Takım 2" in sec
    assert ("Berabere" in sec) or ("E0 Takım 1</td>" in sec) or ("E0 Takım 2</td>" in sec)


def test_market_help_tooltips_and_legend(client_seeded):
    from app.model.markets import MARKET_HELP
    lst = client_seeded.get("/?gun=3gun").text
    assert "Bahis türleri ne demek?" in lst and MARKET_HELP["CS12"] in lst
    det = client_seeded.get(f"/mac/{client_seeded.match_ids[0]}").text
    assert f'data-tip="{MARKET_HELP["DEPUST15"]}"' in det
    assert '/static/app.js' in det and 'title="' + MARKET_HELP['DEPUST15'] not in det


def test_match_list_shows_extra_lines(client_seeded):
    lst = client_seeded.get("/?gun=3gun").text
    for label in ("Toplam 1.5 Üst", "Ev golleri 1.5 Üst", "Dep. golleri 1.5 Üst"):
        assert label in lst


def test_detail_markets_grouped(client_seeded):
    det = client_seeded.get(f"/mac/{client_seeded.match_ids[0]}").text
    for title in ("Maç sonucu", "Çifte şans", "Toplam gol", "Karşılıklı gol", "golleri (ev sahibi)", "golleri (deplasman)"):
        assert title in det
    # toplam gol ve tek takımın golleri karışmasın: satırlarda çıplak "1.5 Alt" yok
    assert "Toplam gol 1.5 Alt" in det and ">1.5 Alt<" not in det


def test_recent_matches_explain_score_and_result_letters(client_seeded):
    det = client_seeded.get(f"/mac/{client_seeded.match_ids[0]}").text
    assert "Atılan–yenen" in det and "G: galibiyet, B: beraberlik, M: mağlubiyet" in det


def test_brand_and_disclaimer_on_every_page(client_seeded):
    for url in ("/", "/kupon", "/kuponlarim", "/performans", "/kasalar", "/ayarlar"):
        text = client_seeded.get(url).text
        assert "<title>" in text and "FutbolSonuçTahmin" in text, url
        assert "Sorumluluk reddi" in text and "makine öğrenmesi" in text, url
        assert "sorumluluğu tamamen size aittir" in text, url


def test_no_gambling_words_in_brand():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8").lower()
    for word in ("bahis tahmin", "kumar", "iddaa"):
        assert word not in base, word


def test_api_key_input_is_masked(client_seeded):
    text = client_seeded.get("/ayarlar").text
    assert 'type="password" name="api_key"' in text


def test_empty_day_points_to_next_match_day(client_seeded):
    text = client_seeded.get("/?gun=bugun").text
    assert "Bu aralıkta maç yok" in text
    assert "Sıradaki maçlar" in text and "4 maç" in text  # 5 maçın 4ü aynı gün (TR saatiyle) and 'href="/?gun=7gun"' in text


def test_seven_day_view_is_offered_and_lists_matches(client_seeded):
    text = client_seeded.get("/?gun=7gun").text
    assert ">7 gün<" in text and "E0 Takım 1" in text


def test_footer_has_owner_and_disclaimer_dialog(client_seeded):
    text = client_seeded.get("/").text
    footer = text[text.index('<footer class="site-footer">'):]
    assert "© 2026 Mehmet Uslu" in footer
    assert 'href="#sorumluluk" data-modal="sorumluluk"' in footer
    assert '<dialog class="modal" id="sorumluluk"' in footer and "Sorumluluk reddi" in footer


def test_header_has_brand_and_mobile_menu(client_seeded):
    text = client_seeded.get("/").text
    head = text[text.index('<header class="topbar">'):text.index("</header>")]
    assert 'class="brand-name"' in head and 'aria-controls="ana-menu"' in head and 'id="ana-menu"' in head


def test_schedule_only_match_says_odds_come_later(client_seeded, session_factory):
    from app.models import OddsQuote
    with session_factory() as s:
        mid = client_seeded.match_ids[0]
        s.query(OddsQuote).filter_by(match_id=mid).delete()
        s.get(Match, mid).api_fixture_id = None
        s.get(Match, client_seeded.match_ids[1]).api_fixture_id = 777
        s.query(OddsQuote).filter_by(match_id=client_seeded.match_ids[1]).delete()
        s.commit()
    text = client_seeded.get("/?gun=3gun").text
    assert text.count(">oran 1 gün önce gelir<") == 1  # sadece API'den gelmemiş maç
    assert text.count(">oran yok<") == 1  # API'den gelmiş ama oranı olmayan maç


def test_theme_toggle_and_early_theme_script(client_seeded):
    text = client_seeded.get("/").text
    head = text[:text.index("</head>")]
    assert "data-theme" in head and "localStorage.getItem('tema')" in head  # sayfa çizilmeden tema seçilir
    assert 'class="theme-toggle"' in text


def test_performance_has_one_chart_with_wallet_picker(client_seeded, session_factory):
    from app.models import Wallet
    with session_factory() as s:
        s.add(Wallet(name="Yeni Deneme Kasası", owner="user", start_balance=50_000, balance=50_000))
        s.commit()
        n = s.query(Wallet).count()
    text = client_seeded.get("/performans").text
    assert text.count('id="kasaChart"') == 1  # tek grafik
    assert text.count('class="wtab"') == n  # her kasa seçicide, yeni açılan da
    assert "Yeni Deneme Kasası" in text and "Model ayrıntıları" in text
