"""Kupon oluşturma, Kuponlarım ve Kasalar sayfaları."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.betting.coupon import build_coupons, save_coupon
from app.betting.selection import eligible, match_candidates
from app.betting.settlement import wallet_stats
from app.betting.wallets import OWNER_LABELS, WalletError, create_wallet, delete_wallet, place_bet, reset_wallet
from app.config import TZ, get_settings
from app.models import Bet, Coupon, Match, Wallet
from app.web.views import DAY_LABELS, day_window

router = APIRouter()
STATUS_LABELS = {"draft": "Taslak", "open": "Açık", "won": "Tuttu", "lost": "Tutmadı", "void": "İptal"}
TARGETS = [2, 3, 5, 10]


def _render(request, name, ctx):
    return request.app.state.render(request, name, ctx)


def _go(url: str, hata: str | None = None, mesaj: str | None = None) -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if hata:
        url += f"{sep}hata={quote(hata)}"
    elif mesaj:
        url += f"{sep}mesaj={quote(mesaj)}"
    return RedirectResponse(url, status_code=303)


def _competitions(session):
    from app.models import Competition
    return session.query(Competition).filter(Competition.kind != "hidden").all()


def _available_leagues(s, now) -> list[tuple[str, str, int]]:
    """Önümüzdeki günlerde oranı ve tahmini olan maçı bulunan ligler: (kod, ad, maç sayısı)."""
    start, end = day_window(now, "3gun")
    cands = match_candidates(s, max(start, now), end, get_settings(s))
    counts: dict[str, list] = {}
    for m in s.query(Match).filter(Match.id.in_(list(cands))):
        entry = counts.setdefault(m.competition.code, [m.competition.name, 0])
        entry[1] += 1
    return [(code, name, n) for code, (name, n) in sorted(counts.items(), key=lambda kv: -kv[1][1])]


@router.get("/kupon", response_class=HTMLResponse)
def coupon_form(request: Request):
    with request.app.state.sf() as s:
        return _render(request, "kupon.html", {"nav": "kupon", "targets": TARGETS, "day_labels": DAY_LABELS,
                                               "leagues": _available_leagues(s, request.app.state.clock()),
                                               "result": None})


def _drafts_cleanup(s, now):
    for c in s.query(Coupon).filter(Coupon.status == "draft", Coupon.created_at < now - timedelta(hours=24)).all():
        s.delete(c)
    s.commit()


@router.post("/kupon/olustur", response_class=HTMLResponse)
def coupon_create(request: Request, hedef: str = Form("3"), mod: str = Form("safe"), gun: str = Form("3gun"),
                  lig: list[str] | None = Form(None)):
    now = request.app.state.clock()
    with request.app.state.sf() as s:
        _drafts_cleanup(s, now)
        settings = get_settings(s)
        try:
            target = float(str(hedef).replace(",", "."))
        except ValueError:
            target = 0
        mode = mod if mod in ("safe", "value") else "safe"
        reason = None
        coupons = []
        if target < 1.1 or target > 1000:
            reason = "Hedef oran 1,10 ile 1000 arasında olmalı."
        else:
            start, end = day_window(now, gun)
            start = max(start, now)
            all_cands = match_candidates(s, start, end, settings)
            cands = all_cands
            if lig:
                allowed = {m.id for m in s.query(Match).filter(Match.id.in_(list(cands))) if m.competition.code in lig}
                cands = {k: v for k, v in cands.items() if k in allowed}
            available = sorted({m.competition.name for m in s.query(Match).filter(Match.id.in_(list(all_cands)))})
            pool = {k: eligible(v, mode, settings) for k, v in cands.items()}
            pool = {k: v for k, v in pool.items() if v}
            drafts = build_coupons(pool, target, settings.coupon_max_picks, settings.coupon_upper_factor)
            play_date = now.astimezone(TZ).date()
            coupons = [save_coupon(s, d, mode, target, "user", play_date, status="draft") for d in drafts]
            if not cands and all_cands:
                reason = ("Seçtiğin liglerde bu aralıkta oranlı maç yok. Oranlı maçı olan ligler: "
                          + ", ".join(available) + ". Lig seçimini kaldırıp tekrar deneyin.")
            elif not cands:
                reason = ("Bu aralıkta oranı ve tahmini olan maç yok. Ücretsiz API planı yalnızca bugünün ve "
                          "yarının maçlarını verir; oranlar her sabah 10:30'da güncellenir.")
            elif not coupons:
                reason = ("Bu aralıkta değerli seçim yetersiz; hedef oranı düşürün ya da daha uzun bir aralık seçin."
                          if mode == "value" else
                          "Hedef orana ulaşılamadı; hedefi değiştirin ya da daha uzun bir aralık seçin.")
        wallets = s.query(Wallet).filter_by(owner="user", busted=False).order_by(Wallet.id).all()
        ctx = {"coupons": coupons, "reason": reason, "wallets": wallets, "mode": mode}
        if request.headers.get("HX-Request"):
            return request.app.state.templates.TemplateResponse(request, "partials/_coupons.html", ctx)
        return _render(request, "kupon.html", {"nav": "kupon", "targets": TARGETS, "day_labels": DAY_LABELS,
                                               "leagues": _available_leagues(s, now), "result": ctx})


@router.post("/kupon/{coupon_id}/oyna")
def coupon_play(request: Request, coupon_id: int, wallet_id: int = Form(...), tutar: str = Form(...)):
    now = request.app.state.clock()
    with request.app.state.sf() as s:
        coupon = s.get(Coupon, coupon_id)
        wallet = s.get(Wallet, wallet_id)
        if coupon is None or wallet is None or wallet.owner != "user":
            raise HTTPException(404)
        if coupon.status not in ("draft", "open"):
            return _go("/kupon", hata="Bu kupon artık oynanamaz.")
        for p in coupon.picks:
            m = s.get(Match, p.match_id)
            if m.status != "scheduled" or m.kickoff_utc <= now:
                return _go("/kupon", hata="Maç başladı, bu kupon oynanamaz.")
        try:
            stake = int(round(float(str(tutar).replace(",", ".")) * 100))
            place_bet(s, wallet, coupon, stake)
        except ValueError:
            return _go("/kupon", hata="Tutar sayı olmalı.")
        except WalletError as exc:
            return _go("/kupon", hata=str(exc))
        coupon.status = "open"
        s.commit()
        return _go(f"/kuponlarim?kasa={wallet.id}", mesaj="Kupon oynandı.")


def autoplay_status(s, now) -> dict:
    """Sistemin bugünkü otomatik oyunu: oynandı mı, kaç kupon, oynanmadıysa neden."""
    from datetime import datetime

    from app.config import get_value
    from app.web.views import next_match_day

    today = now.astimezone(TZ).date()
    rep = get_value(s, "last_autoplay") or {}
    at = datetime.fromisoformat(rep["at"]) if rep.get("at") else None
    ran_today = at is not None and at.astimezone(TZ).date() == today
    return {"ran_today": ran_today, "at": at, "coupons": rep.get("coupons", 0) if ran_today else 0,
            "singles": rep.get("singles", 0) if ran_today else 0,
            "before_hour": now.astimezone(TZ).hour < 11, "next_day": next_match_day(s, now)}


@router.get("/kuponlarim", response_class=HTMLResponse)
def my_coupons(request: Request, kasa: int | None = None, durum: str = "hepsi"):
    with request.app.state.sf() as s:
        wallets = s.query(Wallet).order_by(Wallet.owner != "user", Wallet.id).all()
        current = s.get(Wallet, kasa) if kasa else (wallets[0] if wallets else None)
        bets = []
        stats = None
        if current is not None:
            q = s.query(Bet).join(Coupon, Bet.coupon_id == Coupon.id).filter(Bet.wallet_id == current.id)
            if durum == "acik":
                q = q.filter(Coupon.status == "open")
            elif durum == "sonuc":
                q = q.filter(Coupon.status.in_(("won", "lost", "void")))
            bets = q.order_by(Bet.placed_at.desc()).limit(200).all()
            stats = wallet_stats(s, current)
        return _render(request, "kuponlarim.html", {"nav": "kuponlarim", "wallets": wallets, "current": current,
                                                    "bets": bets, "stats": stats, "durum": durum,
                                                    "autoplay": autoplay_status(s, request.app.state.clock()),
                                                    "status_labels": STATUS_LABELS, "owner_labels": OWNER_LABELS})


@router.get("/kasalar", response_class=HTMLResponse)
def wallets_page(request: Request):
    with request.app.state.sf() as s:
        rows = [(w, wallet_stats(s, w)) for w in s.query(Wallet).order_by(Wallet.owner, Wallet.start_balance)]
        return _render(request, "kasalar.html", {"nav": "kasalar", "rows": rows, "owner_labels": OWNER_LABELS})


@router.post("/kasalar")
def wallet_create(request: Request, ad: str = Form(""), oynayan: str = Form("user"), baslangic: str = Form("0"),
                  kural: str = Form("percent"), deger: str = Form("3")):
    with request.app.state.sf() as s:
        try:
            w = create_wallet(s, ad, oynayan, int(float(baslangic)), stake_rule=kural, stake_value=float(deger))
        except ValueError:
            return _go("/kasalar", hata="Başlangıç parası ve tutar sayı olmalı.")
        except WalletError as exc:
            return _go("/kasalar", hata=str(exc))
        return _go("/kasalar", mesaj=f"{w.name} açıldı.")


@router.post("/kasalar/{wallet_id}/kural")
def wallet_rule(request: Request, wallet_id: int, kural: str = Form("percent"), deger: str = Form("3")):
    with request.app.state.sf() as s:
        w = s.get(Wallet, wallet_id)
        if w is None:
            raise HTTPException(404)
        try:
            value = float(str(deger).replace(",", "."))
        except ValueError:
            value = 0
        if kural not in ("percent", "fixed") or value <= 0 or (kural == "percent" and value > 100):
            return _go("/kasalar", hata="Geçersiz tutar kuralı: yüzde 0–100 arası, sabit tutar 0'dan büyük olmalı.")
        w.stake_rule, w.stake_value = kural, value
        s.commit()
        return _go("/kasalar", mesaj=f"{w.name} tutar kuralı güncellendi.")


@router.post("/kasalar/{wallet_id}/ad")
def wallet_rename(request: Request, wallet_id: int, ad: str = Form("")):
    with request.app.state.sf() as s:
        w = s.get(Wallet, wallet_id)
        if w is None:
            raise HTTPException(404)
        name = " ".join(ad.split())[:60]
        if not name:
            return _go("/kasalar", hata="Kasa adı boş olamaz.")
        old_name, w.name = w.name, name
        s.commit()
        return _go("/kasalar", mesaj=f"{old_name} kasasının adı değişti: {name}.")


@router.post("/kasalar/{wallet_id}/sifirla")
def wallet_reset(request: Request, wallet_id: int):
    with request.app.state.sf() as s:
        w = s.get(Wallet, wallet_id)
        if w is None:
            raise HTTPException(404)
        reset_wallet(s, w)
        return _go("/kasalar", mesaj=f"{w.name} sıfırlandı.")


@router.post("/kasalar/{wallet_id}/sil")
def wallet_delete(request: Request, wallet_id: int):
    with request.app.state.sf() as s:
        w = s.get(Wallet, wallet_id)
        if w is None:
            raise HTTPException(404)
        name = w.name
        delete_wallet(s, w)
        return _go("/kasalar", mesaj=f"{name} silindi.")
