"""API-Football'a hangi ortamdan istek atılabileceği: GitHub Actions ve Render paylaşılan IP kullanır, atmaz."""
import pytest

from app import jobs
from app.config import api_allowed, set_value


@pytest.mark.parametrize("env", [{"BAHIS_NO_API": "1"}, {"GITHUB_ACTIONS": "true"}, {"RENDER": "true"}])
def test_shared_ip_environments_do_not_use_api(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert api_allowed() is False


def test_home_computer_uses_api():
    assert api_allowed() is True


def test_make_api_returns_none_where_api_is_off(session, monkeypatch):
    set_value(session, "api_key", "KEY")
    assert jobs.make_api(session) is not None
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert jobs.make_api(session) is None
