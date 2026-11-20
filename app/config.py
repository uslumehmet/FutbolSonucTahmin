"""Uygulama ayarları, yarışma listesi ve yol sabitleri."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields, replace
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
MODELS_DIR = DATA_DIR / "models"
TZ = ZoneInfo("Europe/Istanbul")

load_dotenv(BASE_DIR / ".env")
LOCAL_DB_URL = f"sqlite:///{(DATA_DIR / 'bahis.db').as_posix()}"


def normalize_db_url(url: str) -> str:
    """Neon/Render'ın verdiği 'postgres://' ya da 'postgresql://' adresini psycopg 3 sürücüsüne bağlar."""
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def resolve_db_url() -> str:
    """Bulutta DATABASE_URL (PostgreSQL), bilgisayarda data/bahis.db (SQLite)."""
    url = os.environ.get("DATABASE_URL") or os.environ.get("BAHIS_DB_URL")
    return normalize_db_url(url) if url else LOCAL_DB_URL


DB_URL = resolve_db_url()


def site_password() -> str | None:
    """Siteye giriş şifresi. Boşsa giriş istenmez (bilgisayarda yerel kullanım)."""
    pw = os.environ.get("SITE_PASSWORD", "").strip()
    return pw or None


@dataclass(frozen=True)
class League:
    code: str
    name: str
    country: str
    api_id: int | None
    kind: str  # "club" | "national" | "hidden"


CLUB_LEAGUES = [
    League("T1", "Süper Lig", "Türkiye", 203, "club"),
    League("E0", "Premier League", "İngiltere", 39, "club"),
    League("SP1", "La Liga", "İspanya", 140, "club"),
    League("I1", "Serie A", "İtalya", 135, "club"),
    League("D1", "Bundesliga", "Almanya", 78, "club"),
    League("F1", "Ligue 1", "Fransa", 61, "club"),
]

NATIONAL_COMPETITIONS = [
    League("WC", "Dünya Kupası", "Dünya", 1, "national"),
    League("WCQ_EU", "Dünya Kupası Avrupa Elemeleri", "Avrupa", 32, "national"),
    League("EURO", "Avrupa Şampiyonası", "Avrupa", 4, "national"),
    League("EUROQ", "Avrupa Şampiyonası Elemeleri", "Avrupa", 960, "national"),
    League("UNL", "UEFA Uluslar Ligi", "Avrupa", 5, "national"),
]

INTL_HIST = League("INTL_HIST", "Milli maç geçmişi", "Dünya", None, "hidden")

ALL_COMPETITIONS = CLUB_LEAGUES + NATIONAL_COMPETITIONS + [INTL_HIST]
COMPETITION_BY_CODE = {c.code: c for c in ALL_COMPETITIONS}
COMPETITION_BY_API_ID = {c.api_id: c for c in ALL_COMPETITIONS if c.api_id is not None}


@dataclass(frozen=True)
class Settings:
    safe_min_odds: float = 1.20
    value_min_prob: float = 0.40
    value_min_edge: float = 0.05
    coupon_max_picks: int = 6
    coupon_upper_factor: float = 1.3
    odds_adjust_pct: float = 0.0
    autoplay_targets: tuple[float, ...] = (2.0, 3.0, 5.0)
    api_daily_limit: int = 100
    default_stake_pct: float = 3.0
    max_model_gap: float = 0.15
    safe_skip_low_data: bool = True  # verisi az maçlar ("veri az") Güvenli moda girmez
    use_market: bool = True  # tahmin, oran gelince bahisçi oranıyla birleştirilir (app/model/market_layer.py)


DEFAULT_SETTINGS = Settings()
_SETTINGS_KEY = "settings"


def get_settings(session) -> Settings:
    from app.models import SettingRow

    row = session.get(SettingRow, _SETTINGS_KEY)
    if row is None or not row.value:
        return DEFAULT_SETTINGS
    known = {f.name for f in fields(Settings)}
    data = {k: v for k, v in row.value.items() if k in known}
    if "autoplay_targets" in data:
        data["autoplay_targets"] = tuple(float(x) for x in data["autoplay_targets"])
    return replace(DEFAULT_SETTINGS, **data)


def save_settings(session, **changes) -> Settings:
    from app.models import SettingRow

    new = replace(get_settings(session), **changes)
    value = asdict(new)
    value["autoplay_targets"] = list(new.autoplay_targets)
    row = session.get(SettingRow, _SETTINGS_KEY)
    if row is None:
        session.add(SettingRow(key=_SETTINGS_KEY, value=value))
    else:
        row.value = value
    session.commit()
    return new


def get_value(session, key: str, default=None):
    from app.models import SettingRow

    row = session.get(SettingRow, key)
    return default if row is None else row.value


def set_value(session, key: str, value) -> None:
    from app.models import SettingRow

    row = session.get(SettingRow, key)
    if row is None:
        session.add(SettingRow(key=key, value=value))
    else:
        row.value = value
    session.commit()


def api_key(session=None) -> str | None:
    if session is not None:
        stored = get_value(session, "api_key")
        if stored:
            return str(stored)
    key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    return key or None


def current_season_start(today: date) -> int:
    return today.year if today.month >= 7 else today.year - 1


def season_code(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"
