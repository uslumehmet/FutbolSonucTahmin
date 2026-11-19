"""Performans ve Ayarlar sayfaları."""
from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.betting.settlement import wallet_stats
from app.betting.stats import calibration_rows, live_calibration, mode_stats, singles_stats
from app.config import api_key, get_settings, get_value, save_settings, set_value
from app.data.api_football import ApiFootball, probe
from app.data.ingest import competition_teams
from app.data.teams import confirm_new_team, merge_team
from app.model.predict import repredict
from app.models import BacktestRun, Coupon, Match, Team, Wallet

router = APIRouter()
GROUP_LABELS = {"1X2": "Maç sonucu (1-X-2)", "OU25": "2.5 Alt/Üst", "BTTS": "Karşılıklı gol"}


def _go(url, hata=None, mesaj=None):
    sep = "&" if "?" in url else "?"
    if hata:
        url += f"{sep}hata={quote(hata)}"
    elif mesaj:
        url += f"{sep}mesaj={quote(mesaj)}"
    return RedirectResponse(url, status_code=303)


@router.get("/performans", response_class=HTMLResponse)
def performance(request: Request):
    with request.app.state.sf() as s:
        wallets = s.query(Wallet).order_by(Wallet.owner, Wallet.start_balance).all()
        series = []
        rows = []
        for w in wallets:
            st = wallet_stats(s, w)
            rows.append((w, st))
            points = [{"x": int(ts.timestamp() * 1000), "y": round(v / 100, 2)} for ts, v in st.equity]
            now_ms = int(request.app.state.clock().timestamp() * 1000)
            if not points or points[-1]["x"] < now_ms:  # çizgi bugüne kadar uzasın
                points.append({"x": now_ms, "y": points[-1]["y"] if points else round(w.start_balance / 100, 2)})
            series.append({"id": w.id, "label": w.name, "owner": w.owner, "start": round(w.start_balance / 100, 2),
                           "data": points})
        modes = {m: mode_stats(s, m) for m in ("safe", "value")}
        singles = {m: {"total": singles_stats(s, m), "market": singles_stats(s, m, "market"),
                       "league": singles_stats(s, m, "league")} for m in ("safe", "value")}
        cal_rows = calibration_rows(s)
        calib = {g: live_calibration(s, g, rows=cal_rows) for g in GROUP_LABELS}
        bt = s.query(BacktestRun).order_by(BacktestRun.id.desc()).first()
        settled = s.query(Coupon).filter(Coupon.source == "auto", Coupon.status.in_(("won", "lost", "void"))).count()
        ctx = {"nav": "performans", "rows": rows, "series_json": json.dumps(series), "modes": modes,
               "singles": singles, "calib": calib, "bt": bt.report if bt else None, "group_labels": GROUP_LABELS,
               "settled": settled, "l2_active": get_value(s, "layer2_active", {}) or {},
               "l2_metrics": get_value(s, "layer2_metrics", {}) or {}}
        return request.app.state.render(request, "performans.html", ctx)


def _unmatched(s):
    out = []
    for team in s.query(Team).filter_by(is_temp=True).all():
        m = s.query(Match).filter((Match.home_team_id == team.id) | (Match.away_team_id == team.id)).first()
        if m is None:
            continue
        cands = sorted((t for t in competition_teams(s, m.competition) if not t.is_temp), key=lambda t: t.name)
        out.append({"team": team, "competition": m.competition, "candidates": cands})
    return out


def _settings_ctx(s, probe_result=None):
    api = ApiFootball(s, api_key(s) or "-", daily_limit=get_settings(s).api_daily_limit)
    key = api_key(s)
    return {"nav": "ayarlar", "s": get_settings(s), "key_hint": (key[:4] + "…" + key[-2:]) if key else None,
            "used": api.used_today(), "limit": api.daily_limit, "month_mb": api.month_bytes() / 1_048_576,
            "last_update": get_value(s, "last_update"), "last_settle": get_value(s, "last_settle"),
            "last_autoplay": get_value(s, "last_autoplay"), "unmatched": _unmatched(s), "probe": probe_result,
            "system_wallets": s.query(Wallet).filter(Wallet.owner != "user").order_by(Wallet.owner, Wallet.id).all(),
            "pick_range": _pick_range(s)}


def _pick_range(s) -> dict:
    """Şimdiye kadar otomatik kuponlardaki seçimlerin tutma ihtimali: en düşük, ortalama, en yüksek (mod başına)."""
    from sqlalchemy import func

    from app.models import CouponPick
    rows = (s.query(Coupon.mode, func.min(CouponPick.prob), func.avg(CouponPick.prob), func.max(CouponPick.prob),
                    func.count(CouponPick.id))
            .join(CouponPick, CouponPick.coupon_id == Coupon.id)
            .filter(Coupon.source == "auto", Coupon.min_prob.is_(None)).group_by(Coupon.mode).order_by(Coupon.mode))
    return {mode: {"min": lo, "avg": avg, "max": hi, "n": n} for mode, lo, avg, hi, n in rows}


@router.get("/ayarlar", response_class=HTMLResponse)
def settings_page(request: Request):
    with request.app.state.sf() as s:
        return request.app.state.render(request, "ayarlar.html", _settings_ctx(s))


FIELDS = {"safe_min_odds": float, "value_min_prob": float, "value_min_edge": float, "odds_adjust_pct": float,
          "coupon_max_picks": int, "coupon_upper_factor": float, "api_daily_limit": int, "max_model_gap": float}


@router.post("/ayarlar")
async def settings_save(request: Request):
    form = await request.form()
    changes = {}
    try:
        for name, cast in FIELDS.items():
            raw = str(form.get(name, "")).strip().replace(",", ".") if name != "autoplay_targets" else ""
            if raw:
                changes[name] = cast(float(raw)) if cast is int else cast(raw)
        targets = str(form.get("autoplay_targets", "")).strip()
        if targets:
            changes["autoplay_targets"] = tuple(float(t.strip().replace(",", ".")) for t in targets.split(",")
                                                if t.strip())
    except ValueError:
        return _go("/ayarlar", hata="Geçersiz değer: sayı girin (ör. 1.20).")
    if form.get("safe_skip_low_data_sent"):  # yalnızca bu kutuyu içeren form gönderilince değişir
        changes["safe_skip_low_data"] = bool(form.get("safe_skip_low_data"))
    if changes.get("coupon_max_picks", 6) < 1 or any(t < 1.1 for t in changes.get("autoplay_targets", (2.0,))):
        return _go("/ayarlar", hata="Geçersiz değer: maç sayısı en az 1, hedef oran en az 1,10 olmalı.")
    with request.app.state.sf() as s:
        save_settings(s, **changes)
    return _go("/ayarlar", mesaj="Ayarlar kaydedildi.")


@router.post("/ayarlar/anahtar")
def settings_key(request: Request, api_key_value: str = Form("", alias="api_key")):
    with request.app.state.sf() as s:
        set_value(s, "api_key", api_key_value.strip() or None)
    return _go("/ayarlar", mesaj="API anahtarı kaydedildi." if api_key_value.strip() else "API anahtarı silindi.")


@router.post("/ayarlar/probe", response_class=HTMLResponse)
def settings_probe(request: Request):
    with request.app.state.sf() as s:
        key = api_key(s)
        if not key:
            return _go("/ayarlar", hata="Önce API anahtarını girin.")
        api = ApiFootball(s, key, daily_limit=get_settings(s).api_daily_limit)
        result = probe(api, request.app.state.clock().date())
        return request.app.state.render(request, "ayarlar.html", _settings_ctx(s, result))


@router.post("/ayarlar/eslestir")
def settings_merge(request: Request, temp_id: int = Form(...), hedef: str = Form(...)):
    now = request.app.state.clock()
    with request.app.state.sf() as s:
        if hedef == "yeni":
            confirm_new_team(s, temp_id)
            team_id = temp_id
        else:
            team_id = int(hedef)
            merge_team(s, temp_id, team_id)
        soon = (s.query(Match).filter(((Match.home_team_id == team_id) | (Match.away_team_id == team_id)),
                                      Match.status == "scheduled", Match.unmatched.is_(False),
                                      Match.kickoff_utc >= now, Match.kickoff_utc <= now + timedelta(days=3)).all())
        for m in soon:
            repredict(s, m.id, now)
    return _go("/ayarlar", mesaj="Takım eşleştirildi.")
