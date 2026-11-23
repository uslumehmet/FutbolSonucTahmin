"""Web uygulaması (FastAPI + Jinja2 + HTMX)."""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import COMPETITION_BY_CODE, DB_URL, TZ, api_key, site_password
from app.db import init_db, make_engine, make_session_factory
from app.model.injuries import LEVELS, ROLES, manual_impact
from app.model.predict import repredict
from app.models import Competition, Match, MissingPlayer
from app.web import views

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent
MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def pct(p) -> str:
    return "–" if p is None else f"%{round(p * 100)}"


def odds_fmt(o) -> str:
    return "–" if o is None else f"{o:.2f}"


def tl(kurus) -> str:
    if kurus is None:
        return "–"
    sign = "-" if kurus < 0 else ""
    k = abs(int(kurus))
    whole, frac = divmod(k, 100)
    s = f"{whole:,}".replace(",", ".")
    return f"{sign}{s},{frac:02d} ₺" if frac else f"{sign}{s} ₺"


def local_dt(dt) -> str:
    if dt is None:
        return ""
    d = dt.astimezone(TZ)
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d:%H:%M}"


def local_day(dt) -> str:
    d = dt.astimezone(TZ)
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def local_date(dt) -> str:
    d = dt.astimezone(TZ) if hasattr(dt, "astimezone") and getattr(dt, "tzinfo", None) else dt
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def local_time(dt) -> str:
    return dt.astimezone(TZ).strftime("%H:%M")


def local_iso(s) -> str:
    """İş raporlarındaki ISO zaman metnini Türkiye saatiyle yazar (saat dilimi yoksa UTC sayılır)."""
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return str(s)
    return local_dt(d if d.tzinfo else d.replace(tzinfo=UTC))


def signed_pct(v) -> str:
    if v is None:
        return "–"
    n = round(v * 100)  # işaret yuvarlanmış sayıya göre: −0,3 "−%0" değil "%0" yazılır
    return "%0" if n == 0 else f"{'+' if n > 0 else '−'}%{abs(n)}"


def make_templates() -> Jinja2Templates:
    t = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    t.env.filters.update(pct=pct, odds=odds_fmt, tl=tl, local_dt=local_dt, local_iso=local_iso, local_time=local_time,
                         local_date=local_date, local_day=local_day, signed_pct=signed_pct)
    from markupsafe import Markup, escape

    from app.model.markets import MARKET_HELP, MARKET_LABELS, MARKET_SECTIONS, market_label, market_parts

    def market_html(key, home=None, away=None):
        """Bahis adı, altında küçük açıklama: "Ev sahibi 1.5 Alt" / "(Galler golleri)"."""
        main, sub = market_parts(key, home, away)
        sub_html = f'<small class="msub">({escape(sub)})</small>' if sub else ""
        return Markup(f'<span class="mlabel"><b>{escape(main)}</b>{sub_html}</span>')
    static = WEB_DIR / "static"
    asset_v = str(int(max((static / f).stat().st_mtime for f in ("app.css", "app.js"))))  # önbellek kırıcı
    t.env.globals.update(AUTH_ON=lambda: bool(site_password()), ASSET_V=asset_v, MARKET_LABELS=MARKET_LABELS, market_label=market_label, market_html=market_html, market_parts=market_parts, MARKET_HELP=MARKET_HELP, MARKET_SECTIONS=MARKET_SECTIONS,
                         ROLES=ROLES, LEVELS=list(LEVELS), FLAG_TIPS=views.FLAG_TIPS)
    return t


def create_app(session_factory=None, start_scheduler: bool = True, clock=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sf = session_factory
        if sf is None:
            engine = make_engine(DB_URL)
            init_db(engine)
            sf = make_session_factory(engine)
        app.state.sf = sf
        from app.betting.wallets import ensure_default_wallets
        with sf() as s:
            ensure_default_wallets(s)
        sched = None
        if start_scheduler:
            from app.jobs import catch_up, create_scheduler
            sched = create_scheduler(sf)
            sched.start()
            threading.Thread(target=catch_up, args=(sf, app.state.clock()), daemon=True).start()
        yield
        if sched is not None:
            sched.shutdown(wait=False)

    app = FastAPI(title="FutbolSonuçTahmin", lifespan=lifespan)
    app.state.clock = clock or (lambda: datetime.now(UTC))
    app.state.templates = make_templates()
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    from app.web import auth
    app.middleware("http")(auth.auth_middleware)

    async def static_cache(request: Request, call_next):
        # Tarayıcı css/js dosyalarını her sayfa geçişinde yeniden sormasın. Adresinde ?v= olanlar
        # her kurulumda değişir, o yüzden uzun süre saklanabilir.
        resp = await call_next(request)
        if request.url.path.startswith("/static/") and resp.status_code == 200:
            resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if "v" in request.query_params
                                             else "public, max-age=86400")
        return resp

    app.middleware("http")(static_cache)
    app.include_router(auth.router)

    def render(request: Request, name: str, ctx: dict, status: int = 200) -> HTMLResponse:
        with request.app.state.sf() as s:
            has_key = bool(api_key(s))
        base = {"request": request, "nav": ctx.pop("nav", ""), "has_api_key": has_key,
                "hata": request.query_params.get("hata"), "mesaj": request.query_params.get("mesaj")}
        return request.app.state.templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status)

    app.state.render = render

    @app.get("/", response_class=HTMLResponse)
    def matches(request: Request, gun: str = "bugun", lig: str | None = None):
        now = request.app.state.clock()
        with request.app.state.sf() as s:
            groups = views.match_rows(s, now, gun, lig or None)
            comps = [c for c in s.query(Competition).filter(Competition.kind != "hidden")]
            from app.config import get_value
            last = get_value(s, "last_update")
            nxt = None if groups else views.next_match_day(s, now, lig or None)
            return render(request, "matches.html", {"nav": "maclar", "groups": groups, "gun": gun, "lig": lig or "",
                                                     "day_labels": views.MATCH_DAY_LABELS, "comps": comps,
                                                     "last": last, "next_day": nxt})

    @app.get("/mac/{match_id}", response_class=HTMLResponse)
    def match_page(request: Request, match_id: int):
        with request.app.state.sf() as s:
            d = views.match_detail(s, match_id, request.app.state.clock())
            if d is None:
                raise HTTPException(404, "Maç bulunamadı")
            return render(request, "match_detail.html", {"nav": "maclar", **d})

    @app.post("/mac/{match_id}/eksik")
    def add_missing(request: Request, match_id: int, team_id: int = Form(...), player_name: str = Form(...),
                    role: str = Form(...), level: str = Form(...)):
        with request.app.state.sf() as s:
            m = s.get(Match, match_id)
            if m is None:
                raise HTTPException(404)
            if team_id not in (m.home_team_id, m.away_team_id) or role not in ROLES or level not in LEVELS \
                    or not player_name.strip():
                return RedirectResponse(f"/mac/{match_id}?hata=Eksik+oyuncu+bilgisi+geçersiz", status_code=303)
            imp = manual_impact(role, level)
            s.add(MissingPlayer(match_id=m.id, team_id=team_id, player_name=player_name.strip()[:80], source="manual",
                                role=role, level=level, attack_effect=imp.attack_effect,
                                concede_effect=imp.concede_effect, active=True))
            s.commit()
            repredict(s, m.id, request.app.state.clock())
        return RedirectResponse(f"/mac/{match_id}", status_code=303)

    @app.post("/mac/{match_id}/eksik/{mp_id}/kaldir")
    def remove_missing(request: Request, match_id: int, mp_id: int):
        with request.app.state.sf() as s:
            mp = s.get(MissingPlayer, mp_id)
            if mp is not None and mp.match_id == match_id:
                if mp.source == "manual":
                    s.delete(mp)
                else:
                    mp.active = not mp.active
                s.commit()
                repredict(s, match_id, request.app.state.clock())
        return RedirectResponse(f"/mac/{match_id}", status_code=303)

    from app.web import routes_betting, routes_info
    app.include_router(routes_betting.router)
    app.include_router(routes_info.router)
    return app


app = create_app(start_scheduler=os.environ.get("BAHIS_NO_SCHEDULER") != "1")
