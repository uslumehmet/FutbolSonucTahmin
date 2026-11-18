from datetime import datetime, timezone

from app.data.teams import (
    confirm_new_team, get_or_create_team, merge_team, normalize, resolve_team, seed_aliases,
)
from app.models import Competition, Match, Team


def test_normalize():
    assert normalize("Fenerbahçe SK") == "fenerbahce"
    assert normalize("İstanbul Başakşehir FK") == "istanbul basaksehir"
    assert normalize("Nott'm Forest") == "nottm forest"


def test_get_or_create_is_idempotent(session):
    a = get_or_create_team(session, "of", "Galatasaray", "club", "Türkiye")
    assert get_or_create_team(session, "of", "Galatasaray", "club").id == a.id


def test_resolve_fuzzy_and_threshold(session):
    gs = get_or_create_team(session, "of", "Galatasaray", "club")
    fb = get_or_create_team(session, "of", "Fenerbahce", "club")
    assert resolve_team(session, "api", "Galatasaray SK", [gs, fb]).id == gs.id
    assert resolve_team(session, "api", "Kocaelispor", [gs, fb]) is None


def test_seeded_alias(session):
    inter = get_or_create_team(session, "of", "FC Internazionale Milano", "club")
    seed_aliases(session)
    assert resolve_team(session, "api", "Inter", []).id == inter.id


def test_resolve_by_word_containment(session):
    konya = get_or_create_team(session, "of", "Konyaspor", "club")
    other = get_or_create_team(session, "of", "Kasimpasa", "club")
    assert resolve_team(session, "of", "Atiker Konyaspor", [konya, other]).id == konya.id


def test_containment_ambiguous_returns_none(session):
    a = get_or_create_team(session, "of", "Manchester City FC", "club")
    b = get_or_create_team(session, "of", "Manchester United FC", "club")
    assert resolve_team(session, "api", "Manchester", [a, b]) is None


def _match(session, home, away, unmatched=True):
    comp = session.query(Competition).filter_by(code="T1").one()
    m = Match(competition_id=comp.id, season=2026, kickoff_utc=datetime(2026, 11, 20, 17, tzinfo=timezone.utc),
              home_team_id=home.id, away_team_id=away.id, unmatched=unmatched)
    session.add(m)
    session.commit()
    return m


def test_merge_team_clears_unmatched(session):
    canon = get_or_create_team(session, "of", "Kocaeli", "club")
    gs = get_or_create_team(session, "of", "Galatasaray", "club")
    temp = Team(name="Kocaelispor", kind="club", is_temp=True)
    session.add(temp)
    session.commit()
    m = _match(session, temp, gs)
    temp_id = temp.id
    merge_team(session, temp_id, canon.id)
    session.refresh(m)
    assert m.home_team_id == canon.id and m.unmatched is False
    assert session.get(Team, temp_id) is None
    assert resolve_team(session, "api", "Kocaelispor", []).id == canon.id


def test_confirm_new_team(session):
    gs = get_or_create_team(session, "of", "Galatasaray", "club")
    temp = Team(name="Yeni Takim", kind="club", is_temp=True)
    session.add(temp)
    session.commit()
    m = _match(session, temp, gs)
    confirm_new_team(session, temp.id)
    session.refresh(m)
    assert m.unmatched is False and session.get(Team, temp.id).is_temp is False


def test_seed_aliases_heals_existing_temp_team(session):
    turkey = get_or_create_team(session, "intl", "Turkey", "national")
    other = get_or_create_team(session, "intl", "Wales", "national")
    temp = Team(name="Türkiye", kind="national", is_temp=True, api_id=777)
    session.add(temp)
    session.commit()
    from app.data.teams import add_alias
    add_alias(session, temp.id, "api", "Türkiye")
    m = _match(session, temp, other)
    temp_id = temp.id
    seed_aliases(session)
    session.refresh(m)
    assert m.home_team_id == turkey.id and m.unmatched is False
    assert session.get(Team, temp_id) is None
    assert resolve_team(session, "api", "Türkiye", []).id == turkey.id
    assert session.get(Team, turkey.id).api_id == 777


def test_merge_team_removes_planned_duplicate_of_api_match(session):
    """openfootball'dan gelmiş plan maçı + aynı maçın API kaydı (geçici takımla): eşleştirince tek maç kalır."""
    from app.models import MissingPlayer, Prediction

    canon = get_or_create_team(session, "of", "Kocaeli", "club")
    gs = get_or_create_team(session, "of", "Galatasaray", "club")
    temp = Team(name="Kocaelispor", kind="club", is_temp=True)
    session.add(temp)
    session.commit()
    planned = _match(session, gs, canon, unmatched=False)
    planned.status = "scheduled"
    session.add(Prediction(match_id=planned.id, kind="live", probs={"MS1": 0.5}))
    session.add(MissingPlayer(match_id=planned.id, team_id=gs.id, player_name="Elle eklenen", source="manual",
                              role="hücum", level="yüksek", attack_effect=0.9, concede_effect=1.0, active=True))
    api_m = _match(session, gs, temp)
    api_m.api_fixture_id, api_m.status = 9002, "scheduled"
    session.commit()
    planned_id, api_id = planned.id, api_m.id
    merge_team(session, temp.id, canon.id)
    assert session.get(Match, planned_id) is None
    kept = session.get(Match, api_id)
    assert (kept.home_team_id, kept.away_team_id, kept.unmatched) == (gs.id, canon.id, False)
    assert session.query(Prediction).filter_by(match_id=planned_id).count() == 0
    assert session.query(MissingPlayer).filter_by(match_id=api_id, player_name="Elle eklenen").count() == 1
