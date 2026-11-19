"""Komut satırı: python -m app.cli <komut>"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone

from app.config import DB_URL, current_season_start
from app.db import init_db, make_engine, make_session_factory


def _session():
    engine = make_engine(DB_URL)
    init_db(engine)
    return make_session_factory(engine)()


def cmd_history(_args) -> None:
    from app.data.ingest import ingest_openfootball
    from app.data.openfootball import HISTORY_START_YEAR
    from app.models import Competition, Match

    s = _session()
    today = date.today()
    years = list(range(HISTORY_START_YEAR, current_season_start(today) + 1))
    n = ingest_openfootball(s, years, today=today, now=_utcnow())
    print(f"Toplam işlenen maç: {n}")
    for comp in s.query(Competition).filter_by(kind="club"):
        cnt = s.query(Match).filter_by(competition_id=comp.id).count()
        print(f"  {comp.name}: {cnt}")


def cmd_intl(_args) -> None:
    from app.data.ingest import ingest_international

    s = _session()
    print(f"Milli maç: {ingest_international(s)}")


def _api(s):
    from app.config import api_key, get_settings
    from app.data.api_football import ApiFootball

    key = api_key(s)
    if not key:
        print("API anahtarı yok: .env dosyasına API_FOOTBALL_KEY=... yazın veya Ayarlar sayfasından girin.")
        return None
    return ApiFootball(s, key, daily_limit=get_settings(s).api_daily_limit)


def cmd_probe(_args) -> None:
    from app.data.api_football import probe

    s = _session()
    api = _api(s)
    if api is None:
        return
    for k, v in probe(api, date.today()).items():
        print(f"  {k}: {v}")
    print(f"Bugün kullanılan istek: {api.used_today()}/{api.daily_limit}")


def cmd_walkforward(_args) -> None:
    from app.backtest.walkforward import walkforward_club
    from app.config import CLUB_LEAGUES

    s = _session()
    end = current_season_start(date.today())
    for lg in CLUB_LEAGUES:
        n = walkforward_club(s, lg.code, 2016, end)
        print(f"  {lg.name}: {n} yeni geçmiş tahmin")


def cmd_train_layer2(_args) -> None:
    from app.backtest.walkforward import train_layer2_all

    s = _session()
    for group, m in train_layer2_all(s).items():
        durum = "AÇIK" if m.get("kept") else "kapalı"
        print(f"  {group}: {durum}  Katman1 ll={m.get('l1_ll', 0):.4f}  Katman2 ll={m.get('l2_ll', 0):.4f}  n={m.get('n')}")


def cmd_backtest(_args) -> None:
    import json

    from app.backtest.simulate import simulate

    s = _session()
    rep = simulate(s, seasons=[2023, 2024, 2025])
    print(json.dumps({k: rep[k] for k in ("n_matches", "confidence", "layer2")}, ensure_ascii=False, indent=1))
    for g, v in rep["groups"].items():
        print(f"  {g}: isabet {v['accuracy']:.3f}  log-loss model {v['model_ll']:.4f} / temel {v['base_ll']:.4f}  ECE {v['ece']:.4f}")


def _sf():
    engine = make_engine(DB_URL)
    init_db(engine)
    return make_session_factory(engine)


def _utcnow():
    return datetime.now(timezone.utc)


def cmd_init(_args) -> None:
    from app.betting.wallets import ensure_default_wallets

    s = _session()
    ensure_default_wallets(s)
    cmd_history(_args)
    cmd_intl(_args)
    print("Geçmiş tahminler ve makine öğrenmesi katmanı hazırlanıyor (birkaç dakika sürebilir)...")
    cmd_walkforward(_args)
    cmd_train_layer2(_args)
    cmd_backtest(_args)


def cmd_update(_args) -> None:
    from app import jobs

    jobs.daily_update(_sf(), _utcnow())
    with _sf()() as s:
        from app.config import get_value
        print(get_value(s, "last_update"))


def cmd_autoplay(_args) -> None:
    from app.betting.autoplay import run_autoplay

    print(run_autoplay(_session(), _utcnow()))


def cmd_settle(_args) -> None:
    from app import jobs

    jobs.settle_job(_sf(), _utcnow())
    with _sf()() as s:
        from app.config import get_value
        print(get_value(s, "last_settle"))


def cmd_catch_up(_args) -> None:
    """Zamanı gelmiş bütün işleri yapar (GitHub Actions bunu günde birkaç kez çağırır)."""
    from app import jobs

    ran = jobs.catch_up(_sf(), _utcnow(), settle=True)
    print("Çalışan işler: " + (", ".join(ran) if ran else "yok"))
    with _sf()() as s:
        from app.config import get_value
        for key in ("last_update", "last_autoplay", "last_settle"):
            print(f"{key}: {get_value(s, key)}")


def cmd_espn(args) -> None:
    """ESPN'den eksik maç sonuçlarını doldurur.

    Örnek: python -m app.cli espn --sezon 2021 2025 2026 --lig T1 --sinir 400
    --sezon verilmezse bu sezon; --lig verilmezse altı lig; --sinir bu çalışmada en fazla kaç dosya indirileceği.
    """
    from datetime import date

    from app.config import TZ
    from app.data.espn import ingest_espn

    def values(flag):
        if flag not in args.rest:
            return None
        i = args.rest.index(flag) + 1
        out = []
        while i < len(args.rest) and not args.rest[i].startswith("--"):
            out.append(args.rest[i])
            i += 1
        return out

    seasons = [int(x) for x in values("--sezon") or []] or None
    codes = values("--lig")
    limit = int((values("--sinir") or ["300"])[0])
    today = _utcnow().astimezone(TZ).date()
    with _sf()() as s:
        rep = ingest_espn(s, today, codes=codes, seasons=seasons, max_requests=limit)
    print(f"İndirilen dosya: {rep.requests}, işlenen gün: {rep.days}, yazılan sonuç: {rep.results}")
    if rep.stopped:
        print("İstek sınırına gelindi; kalan günler için komutu yeniden çalıştırın.")
    for line in rep.unresolved:
        print("Eşleşmeyen takım:", line)
    for line in rep.errors:
        print("Hata:", line)


def cmd_tasi(args) -> None:
    """Bilgisayardaki SQLite veritabanını HEDEF_DB_URL adresindeki bulut veritabanına kopyalar."""
    import os

    from app.config import LOCAL_DB_URL, normalize_db_url
    from app.migrate import TargetNotEmpty, copy_database

    target = os.environ.get("HEDEF_DB_URL", "").strip()
    if not target:
        print("HEDEF_DB_URL yok. Neon'daki bağlantı adresini .env dosyasına HEDEF_DB_URL=... olarak yazın.")
        return
    print("Kopyalanıyor (birkaç dakika sürebilir)...")
    try:
        counts = copy_database(make_engine(LOCAL_DB_URL), make_engine(normalize_db_url(target)),
                               force="--zorla" in args.rest)
    except TargetNotEmpty as e:
        print(e)
        return
    print(f"Bitti: {sum(counts.values())} satır kopyalandı.")


COMMANDS = {"catch-up": cmd_catch_up, "espn": cmd_espn, "tasi": cmd_tasi, "init": cmd_init, "update": cmd_update, "autoplay": cmd_autoplay, "settle": cmd_settle,
            "history": cmd_history, "intl": cmd_intl, "probe": cmd_probe, "walkforward": cmd_walkforward,
            "train-layer2": cmd_train_layer2, "backtest": cmd_backtest}


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="app.cli")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args, rest = parser.parse_known_args(argv)
    args.rest = rest
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
