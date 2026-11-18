"""Şifreli giriş: site internete açıldığında sadece şifreyi bilen girer."""
import pytest
from fastapi.testclient import TestClient

from app.web import auth
from app.web.main import create_app
from tests.helpers import NOW


@pytest.fixture
def locked_client(session_factory, monkeypatch):
    monkeypatch.setenv("SITE_PASSWORD", "dogru-sifre")
    auth._fails.clear()
    app = create_app(session_factory, start_scheduler=False, clock=lambda: NOW)
    with TestClient(app) as c:
        yield c


def test_without_password_site_is_open(client, monkeypatch):
    monkeypatch.delenv("SITE_PASSWORD", raising=False)
    r = client.get("/")
    assert r.status_code == 200
    assert "Çıkış" not in r.text


def test_pages_redirect_to_login(locked_client):
    for path in ("/", "/kuponlarim", "/ayarlar", "/mac/1"):
        r = locked_client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/giris?sonra=")


def test_htmx_request_gets_hx_redirect(locked_client):
    r = locked_client.post("/kupon/olustur", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/giris"


def test_post_is_blocked_too(locked_client):
    r = locked_client.post("/ayarlar/anahtar", data={"api_key": "x"}, follow_redirects=False)
    assert r.status_code == 303 and "/giris" in r.headers["location"]


def test_login_page_has_disclaimer_and_no_menu(locked_client):
    html = locked_client.get("/giris").text
    assert "Sorumluluk reddi" in html and "FutbolSonuçTahmin" in html
    assert 'id="ana-menu"' not in html


def test_wrong_password(locked_client):
    r = locked_client.post("/giris", data={"sifre": "yanlis", "sonra": "/"}, follow_redirects=False)
    assert r.status_code == 401 and "Şifre yanlış" in r.text
    assert auth.COOKIE not in r.cookies


def test_correct_password_sets_cookie_and_opens_site(locked_client):
    r = locked_client.post("/giris", data={"sifre": "dogru-sifre", "sonra": "/kuponlarim"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/kuponlarim"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    page = locked_client.get("/kuponlarim")
    assert page.status_code == 200 and "Çıkış" in page.text


def test_logout_clears_session(locked_client):
    locked_client.post("/giris", data={"sifre": "dogru-sifre"})
    locked_client.get("/cikis")
    assert locked_client.get("/", follow_redirects=False).status_code == 303


def test_open_redirect_is_blocked(locked_client):
    for bad in ("//kotu.example", "https://kotu.example", "/\\kotu.example"):
        r = locked_client.post("/giris", data={"sifre": "dogru-sifre", "sonra": bad}, follow_redirects=False)
        assert r.headers["location"] == "/"


def test_too_many_failures_lock_out(locked_client):
    for _ in range(auth.MAX_FAILS):
        locked_client.post("/giris", data={"sifre": "yanlis"})
    r = locked_client.post("/giris", data={"sifre": "dogru-sifre"}, follow_redirects=False)
    assert r.status_code == 429


def test_health_and_static_stay_open(locked_client):
    assert locked_client.get("/saglik").json() == {"durum": "ok"}
    assert locked_client.get("/static/app.css").status_code == 200


def test_token_expires_and_password_change_invalidates():
    t = auth.make_token("a", now=1000.0)
    assert auth.token_ok(t, "a", now=1001.0)
    assert not auth.token_ok(t, "b", now=1001.0)
    assert not auth.token_ok(t, "a", now=1000.0 + auth.SESSION_DAYS * 86400 + 1)
    assert not auth.token_ok("bozuk", "a") and not auth.token_ok(None, "a")


def test_forged_cookie_rejected(locked_client):
    locked_client.cookies.set(auth.COOKIE, "99999999999.deadbeef")
    assert locked_client.get("/", follow_redirects=False).status_code == 303


def test_noindex_header(client):
    assert client.get("/").headers["X-Robots-Tag"] == "noindex, nofollow"
