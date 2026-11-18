"""API-Football (v3) istemcisi: önbellek + günlük kota takibi."""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import func

from app.config import get_value, set_value
from app.models import ApiCall

log = logging.getLogger(__name__)
BASE_URL = "https://v3.football.api-sports.io"
BET365_ID = 8


class QuotaExceeded(Exception):
    pass


class ApiError(Exception):
    pass


def _has_errors(errors) -> bool:
    if not errors:
        return False
    if isinstance(errors, dict):
        return any(errors.values())
    return bool(errors)


class ApiFootball:
    def __init__(self, session, api_key: str, daily_limit: int = 100,
                 transport: httpx.BaseTransport | None = None, base_url: str = BASE_URL, clock=None,
                 sleeper=None, per_minute: int = 10):
        self.session = session
        self.api_key = api_key
        self.daily_limit = daily_limit
        self.per_minute = per_minute
        self.base_url = base_url.rstrip("/")
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleeper = sleeper or time.sleep
        self._recent: list[datetime] = []
        self.client = httpx.Client(transport=transport, timeout=30,
                                   headers={"x-apisports-key": api_key, "User-Agent": "futbol-sonuc-tahmin/1.0"})

    # --- temel ---
    @staticmethod
    def params_key(endpoint: str, params: dict) -> str:
        return endpoint + "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))

    def used_today(self) -> int:
        now = self.clock()
        start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        return self.session.query(func.count(ApiCall.id)).filter(
            ApiCall.called_at >= start, ApiCall.status_code != 429).scalar() or 0

    def month_bytes(self) -> int:
        now = self.clock()
        start = datetime(now.year, now.month, 1, tzinfo=UTC)
        return self.session.query(func.coalesce(func.sum(ApiCall.bytes), 0)).filter(ApiCall.called_at >= start).scalar()

    def cached(self, endpoint: str, params: dict, max_age: timedelta) -> dict | None:
        key = self.params_key(endpoint, params)
        row = (
            self.session.query(ApiCall)
            .filter(ApiCall.params_key == key, ApiCall.status_code == 200,
                    ApiCall.called_at >= self.clock() - max_age)
            .order_by(ApiCall.called_at.desc())
            .first()
        )
        return json.loads(row.body) if row and row.body else None  # gövdesi temizlenmiş kayıt: önbellekte yok

    def get(self, endpoint: str, params: dict, max_age: timedelta) -> dict:
        hit = self.cached(endpoint, params, max_age)
        if hit is not None:
            return hit
        if self.used_today() >= self.daily_limit:
            raise QuotaExceeded(f"günlük API sınırı doldu ({self.daily_limit})")
        for attempt in range(2):
            self._throttle()
            try:
                resp = self.client.get(self.base_url + endpoint, params=params)
            except httpx.HTTPError as exc:
                raise ApiError(f"bağlantı hatası: {exc}") from exc
            if attempt == 0 and resp.status_code == 429 and "per-minute" in resp.text:
                self._log_call(endpoint, params, resp)
                self.sleeper(61)  # dakikalık sınır: bir dakika bekleyip bir kez daha dene
                self._recent.clear()
                continue
            break
        return self._handle(endpoint, params, resp)

    def _throttle(self) -> None:
        now = self.clock()
        self._recent = [t for t in self._recent if (now - t).total_seconds() < 60]
        if len(self._recent) >= self.per_minute:
            wait = 60 - (now - self._recent[0]).total_seconds() + 0.5
            self.sleeper(max(wait, 0.5))
            now = self.clock()
            self._recent = [t for t in self._recent if (now - t).total_seconds() < 60]
        self._recent.append(self.clock())

    def _log_call(self, endpoint: str, params: dict, resp: httpx.Response) -> None:
        body = resp.text
        self.session.add(ApiCall(endpoint=endpoint, params_key=self.params_key(endpoint, params),
                                 called_at=self.clock(), status_code=resp.status_code,
                                 bytes=len(resp.content), body=body if resp.status_code == 200 else body[:2000]))
        self.session.commit()

    def _handle(self, endpoint: str, params: dict, resp: httpx.Response) -> dict:
        body = resp.text
        self._log_call(endpoint, params, resp)
        remaining = resp.headers.get("x-ratelimit-requests-remaining")
        if remaining is not None:
            set_value(self.session, "api_remaining", {"value": remaining, "at": self.clock().isoformat()})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 429 or (isinstance(data.get("errors"), dict) and "rateLimit" in data["errors"]):
            raise QuotaExceeded(str(data.get("errors") or "429"))
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code}: {body[:200]}")
        if _has_errors(data.get("errors")):
            # hatalı yanıtı önbellekten düşür ki tekrar denenebilsin
            row = self.session.query(ApiCall).order_by(ApiCall.id.desc()).first()
            row.status_code = 299
            self.session.commit()
            raise ApiError(str(data["errors"]))
        return data

    def get_all_pages(self, endpoint: str, params: dict, max_age: timedelta) -> list[dict]:
        first = self.get(endpoint, params, max_age)
        items = list(first.get("response", []))
        total = int((first.get("paging") or {}).get("total") or 1)
        for page in range(2, total + 1):
            items += self.get(endpoint, {**params, "page": page}, max_age).get("response", [])
        return items

    # --- uçlar ---
    def fixtures_on(self, day: date) -> list[dict]:
        return self.get("/fixtures", {"date": day.isoformat(), "timezone": "UTC"}, timedelta(hours=3)).get("response", [])

    def fixtures_for_league(self, league_id: int, season: int, max_age: timedelta = timedelta(hours=20)) -> list[dict]:
        return self.get("/fixtures", {"league": league_id, "season": season, "timezone": "UTC"}, max_age).get("response", [])

    def odds_for(self, league_id: int, season: int, day: date) -> list[dict]:
        params = {"league": league_id, "season": season, "date": day.isoformat()}
        items = self.get_all_pages("/odds", {**params, "bookmaker": BET365_ID}, timedelta(hours=6))
        if not items:
            items = self.get_all_pages("/odds", params, timedelta(hours=6))
        return items

    def injuries_for(self, league_id: int, season: int, day: date, fixture_ids: list[int]) -> list[dict]:
        strategy = get_value(self.session, "injuries_strategy", "league_date")
        if strategy == "fixture":
            out = []
            for fid in fixture_ids:
                out += self.get("/injuries", {"fixture": fid}, timedelta(hours=6)).get("response", [])
            return out
        return self.get("/injuries", {"league": league_id, "season": season, "date": day.isoformat()},
                        timedelta(hours=6)).get("response", [])

    def odds_for_fixture(self, fixture_id: int) -> list[dict]:
        """Önce yalnızca Bet365 (küçük yanıt, az internet); yoksa bütün bahisçiler."""
        items = self.get("/odds", {"fixture": fixture_id, "bookmaker": BET365_ID}, timedelta(hours=6)).get("response", [])
        if not items:
            items = self.get("/odds", {"fixture": fixture_id}, timedelta(hours=6)).get("response", [])
        return items

    def injuries_on(self, day: date) -> list[dict]:
        return self.get("/injuries", {"date": day.isoformat()}, timedelta(hours=6)).get("response", [])

    def fixtures_on_past(self, day: date) -> list[dict]:
        """Geçmiş bir günün maçları (sonuçlar değişmez; uzun süre önbellekte kalır)."""
        return self.get("/fixtures", {"date": day.isoformat(), "timezone": "UTC"}, timedelta(days=3650)).get("response", [])

    def player_season(self, player_id: int, season: int) -> dict | None:
        resp = self.get("/players", {"id": player_id, "season": season}, timedelta(days=30)).get("response", [])
        return resp[0] if resp else None


def probe(api: ApiFootball, today: date) -> dict[str, str]:
    """Ücretsiz planın neye izin verdiğini dener (~4 istek)."""
    from app.config import COMPETITION_BY_API_ID, current_season_start

    out: dict[str, str] = {}
    season = current_season_start(today)
    try:
        api.fixtures_for_league(39, season)
        out["lig+sezon sorgusu"] = "açık"
    except ApiError as exc:
        out["lig+sezon sorgusu"] = f"kapalı ({exc})"
    except QuotaExceeded as exc:
        return {"hata": str(exc)}
    ours = []
    try:
        for d in (today, today + timedelta(days=1), today + timedelta(days=2)):
            items = api.fixtures_on(d)
            ours += [f for f in items if f["league"]["id"] in COMPETITION_BY_API_ID
                     and f["fixture"]["status"]["short"] in ("NS", "TBD")]
            if ours:
                break
        out["tarihe göre fikstür"] = f"açık ({len(ours)} bizim maç)"
    except (ApiError, QuotaExceeded) as exc:
        out["tarihe göre fikstür"] = f"hata: {exc}"
    if ours:
        try:
            res = api.odds_for_fixture(ours[0]["fixture"]["id"])
            out["maç başına oran"] = "açık" if res else "boş (henüz oran yok)"
        except (ApiError, QuotaExceeded) as exc:
            out["maç başına oran"] = f"hata: {exc}"
    try:
        api.injuries_on(today)
        out["tarihe göre eksikler"] = "açık"
    except (ApiError, QuotaExceeded) as exc:
        out["tarihe göre eksikler"] = f"hata: {exc}"
    return out


CLOUD_BODY_KEEP_DAYS = 10


def prune_old_bodies(session, now, keep_days: int = CLOUD_BODY_KEEP_DAYS) -> int:
    """Bulut veritabanında (ücretsiz plan 0,5 GB) eski API yanıtlarının gövdesini siler.

    İstek kaydı kalır (günlük/aylık sayaçlar bozulmaz). Eski günlerin sonuçları zaten maç tablosunda,
    çekilen geçmiş günler de ayrıca hatırlanıyor; bu yüzden aynı istek tekrar atılmaz.
    """
    cutoff = now - timedelta(days=keep_days)
    n = (session.query(ApiCall).filter(ApiCall.called_at < cutoff, ApiCall.body != "")
         .update({ApiCall.body: ""}, synchronize_session="fetch"))
    session.commit()
    return n
