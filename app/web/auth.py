"""Şifreli giriş: site internete açıldığında sadece şifreyi bilen girer.

SITE_PASSWORD ortam değişkeni boşsa giriş istenmez (bilgisayarda yerel kullanım).
Oturum, imzalı bir çerezde tutulur; şifre değişince eski oturumlar geçersiz olur.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import site_password

COOKIE = "fst_oturum"
SESSION_DAYS = 180
OPEN_PATHS = ("/giris", "/cikis", "/saglik", "/static/", "/favicon.ico")
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60

router = APIRouter()
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _secret(password: str) -> bytes:
    extra = os.environ.get("SECRET_KEY", "")
    return hashlib.sha256(f"futbol-sonuc-tahmin|{extra}|{password}".encode()).digest()


def make_token(password: str, now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(_secret(password), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def token_ok(token: str | None, password: str, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    good = hmac.new(_secret(password), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _locked(ip: str, now: float) -> bool:
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if now - t < LOCK_SECONDS]
        _fails[ip] = recent
        return len(recent) >= MAX_FAILS


def _record_fail(ip: str, now: float) -> None:
    with _fails_lock:
        _fails.setdefault(ip, []).append(now)


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def _safe_next(target: str | None) -> str:
    # Sadece site içi adres: "//başka-site" ya da "http://..." ile dışarı yönlendirme yapılmaz.
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


async def auth_middleware(request: Request, call_next):
    password = site_password()
    path = request.url.path
    if password and not path.startswith(OPEN_PATHS) and not token_ok(request.cookies.get(COOKIE), password):
        if request.headers.get("hx-request"):
            return Response(status_code=401, headers={"HX-Redirect": "/giris"})
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/giris?sonra={quote(target)}", status_code=303)
    response = await call_next(request)
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return response


def _login_page(request: Request, sonra: str, hata: str | None = None, status: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, "giris.html", {"request": request, "sonra": _safe_next(sonra), "giris_hata": hata,
                                "nav": "", "has_api_key": True, "login_page": True},
        status_code=status)


@router.get("/giris", response_class=HTMLResponse)
def login_form(request: Request, sonra: str = "/"):
    if not site_password():
        return RedirectResponse("/", status_code=303)
    return _login_page(request, sonra)


@router.post("/giris")
def login(request: Request, sifre: str = Form(""), sonra: str = Form("/")):
    password = site_password()
    if not password:
        return RedirectResponse("/", status_code=303)
    ip, now = _client_ip(request), time.time()
    if _locked(ip, now):
        return _login_page(request, sonra, "Çok fazla hatalı deneme. 15 dakika sonra tekrar deneyin.", 429)
    if not hmac.compare_digest(sifre.encode(), password.encode()):
        _record_fail(ip, now)
        return _login_page(request, sonra, "Şifre yanlış.", 401)
    resp = RedirectResponse(_safe_next(sonra), status_code=303)
    resp.set_cookie(COOKIE, make_token(password, now), max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=_is_https(request))
    return resp


@router.get("/cikis")
def logout():
    resp = RedirectResponse("/giris" if site_password() else "/", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@router.get("/saglik")
def health():
    """Render'ın 'site ayakta mı' kontrolü; veri göstermez."""
    return {"durum": "ok"}
