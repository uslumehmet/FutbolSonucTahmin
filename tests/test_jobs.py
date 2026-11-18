from datetime import datetime

from app import jobs
from app.config import TZ
from app.models import JobRun


def _stub(monkeypatch):
    ran = []
    for name in ("daily_update", "daily_autoplay", "settle_job", "refresh_sources", "weekly_retrain"):
        monkeypatch.setattr(jobs, name, lambda sf, now, _n=name: ran.append(_n))
    return ran


def test_catch_up_runs_missing_jobs(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    names = jobs.catch_up(session_factory, datetime(2026, 11, 16, 14, 0, tzinfo=TZ))
    assert ran[:3] == ["settle_job", "daily_update", "daily_autoplay"]
    assert set(names) == set(ran)
    assert {"refresh_sources", "weekly_retrain"} <= set(ran)


def test_catch_up_before_11_skips_autoplay(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    jobs.catch_up(session_factory, datetime(2026, 11, 16, 9, 0, tzinfo=TZ))
    assert "daily_autoplay" not in ran and "daily_update" in ran


def test_catch_up_does_not_repeat_done_jobs(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    now = datetime(2026, 11, 16, 14, 0, tzinfo=TZ)
    with session_factory() as s:
        for job in ("autoplay", "update", "refresh", "retrain"):
            s.add(JobRun(job=job, run_key="2026-11-16", finished_at=now))
        s.commit()
    jobs.catch_up(session_factory, now)
    assert ran == ["settle_job"]


def test_catch_up_redoes_update_made_after_midnight(session_factory, monkeypatch):
    """Gece 00:16'daki güncelleme sabah oranlarının yerini tutmaz; 09:07'de yeniden yapılır."""
    ran = _stub(monkeypatch)
    with session_factory() as s:
        s.add(JobRun(job="update", run_key="2026-11-18", finished_at=datetime(2026, 11, 18, 0, 16, tzinfo=TZ)))
        s.commit()
    jobs.catch_up(session_factory, datetime(2026, 11, 18, 9, 7, tzinfo=TZ))
    assert "daily_update" in ran


def test_scheduler_jobs_registered(session_factory):
    sched = jobs.create_scheduler(session_factory)
    ids = {j.id for j in sched.get_jobs()}
    assert ids == {"update", "autoplay", "settle", "settle_night", "retrain", "refresh", "catchup"}
    autoplay = sched.get_job("autoplay")
    fields = {f.name: str(f) for f in autoplay.trigger.fields}
    assert fields["hour"] == "11" and fields["minute"] == "0"
    assert str(sched.timezone) == "Europe/Istanbul"


def test_mark_job_records_run(session_factory):
    now = datetime(2026, 11, 16, 14, 0, tzinfo=TZ)
    jobs.mark_job(session_factory, "update", now)
    jobs.mark_job(session_factory, "update", now)
    with session_factory() as s:
        assert s.query(JobRun).filter_by(job="update").count() == 1


def test_periodic_catch_up_skips_settle(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    jobs.catch_up(session_factory, datetime(2026, 11, 16, 14, 0, tzinfo=TZ), settle=False)
    assert "settle_job" not in ran and "daily_autoplay" in ran


def test_scheduler_has_periodic_catch_up(session_factory):
    sched = jobs.create_scheduler(session_factory)
    job = sched.get_job("catchup")
    assert job is not None and job.trigger.interval.total_seconds() == 1800
