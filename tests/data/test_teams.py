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


def _amed(session):
    """openfootball + API adı olan, lige yeni çıkmış bir takım; bir API maçı ve bir openfootball maçı."""
    from app.data.teams import add_alias

    amed = get_or_create_team(session, "of", "Amed", "club")
    add_alias(session, amed.id, "api", "Amed SK")
    amed.api_id = 3579
    gs = get_or_create_team(session, "of", "Galatasaray", "club")
    api_m = _match(session, amed, gs, unmatched=False)
    api_m.api_fixture_id = 777
    of_m = _match(session, gs, amed, unmatched=False)
    session.commit()
    return amed, gs, api_m, of_m


def test_unmatch_splits_api_identity(session):
    from app.data.teams import unmatch_team
    from app.models import TeamAlias

    amed, gs, api_m, of_m = _amed(session)
    temp_id = unmatch_team(session, amed.id)
    temp = session.get(Team, temp_id)
    assert temp is not None and temp.is_temp and temp.api_id == 3579 and temp.name == "Amed"
    session.refresh(api_m)
    session.refresh(of_m)
    assert api_m.home_team_id == temp_id and api_m.unmatched is True
    assert of_m.away_team_id == amed.id and of_m.unmatched is False
    assert session.get(Team, amed.id).api_id is None
    assert {a.source for a in session.query(TeamAlias).filter_by(team_id=temp_id)} == {"api"}
    assert {a.source for a in session.query(TeamAlias).filter_by(team_id=amed.id)} == {"of"}


def test_rematch_restores_everything(session):
    from app.data.teams import unmatch_team

    amed, gs, api_m, of_m = _amed(session)
    temp_id = unmatch_team(session, amed.id)
    merge_team(session, temp_id, amed.id)
    session.refresh(api_m)
    assert api_m.home_team_id == amed.id and api_m.unmatched is False
    assert session.get(Team, temp_id) is None
    assert session.get(Team, amed.id).api_id == 3579
    assert resolve_team(session, "api", "Amed SK", []).id == amed.id


def test_unmatch_api_only_team_becomes_temp(session):
    from app.data.teams import add_alias, unmatch_team

    t = Team(name="Ümraniyespor", kind="club", api_id=3577)
    session.add(t)
    session.flush()
    add_alias(session, t.id, "api", "Ümraniyespor")
    gs = get_or_create_team(session, "of", "Galatasaray", "club")
    m = _match(session, t, gs, unmatched=False)
    before = session.query(Team).count()
    assert unmatch_team(session, t.id) == t.id
    session.refresh(m)
    assert session.get(Team, t.id).is_temp is True and m.unmatched is True
    assert session.query(Team).count() == before


def test_unmatch_without_api_identity_does_nothing(session):
    from app.data.teams import unmatch_team

    corum = get_or_create_team(session, "of", "Çorum", "club")
    assert unmatch_team(session, corum.id) is None
    assert session.get(Team, corum.id).is_temp is False


def test_api_ingest_after_unmatch_stays_unmatched(session):
    from app.data.ingest_api import _api_team
    from app.data.teams import unmatch_team

    amed, gs, api_m, of_m = _amed(session)
    temp_id = unmatch_team(session, amed.id)
    comp = session.query(Competition).filter_by(code="T1").one()
    assert _api_team(session, comp, "Amed SK", 3579, 2026).id == temp_id


def test_promoted_teams(session):
    from datetime import date

    from app.data.teams import promoted_teams

    comp = session.query(Competition).filter_by(code="T1").one()
    old, new, temp = (get_or_create_team(session, "of", n, "club") for n in ("Galatasaray", "Amed", "Geçici"))
    temp.is_temp = True

    def play(season, home, away):
        session.add(Match(competition_id=comp.id, season=season, home_team_id=home.id, away_team_id=away.id,
                          kickoff_utc=datetime(season, 10, 1, tzinfo=timezone.utc), stats={}))

    play(2025, old, old)
    play(2026, old, new)
    play(2026, temp, old)
    session.commit()
    assert [(c.code, t.name) for c, t in promoted_teams(session, date(2026, 11, 22))] == [("T1", "Amed")]


def _finished(session, home, away, hg, ag, api_fixture_id=None, unmatched=False, stats=None):
    m = _match(session, home, away, unmatched=unmatched)
    m.status, m.home_goals, m.away_goals = "finished", hg, ag
    m.api_fixture_id, m.stats = api_fixture_id, stats or {}
    session.commit()
    return m


def test_merge_team_joins_finished_duplicate(session):
    """Lille–Lyon (21 Kas 2026): ESPN sonucu asıl takımın plan maçına, API kaydı geçici takıma yazılmıştı.
    Eşleştirince tek maç kalır: tahmini, kupon seçimi ve ESPN istatistiği olan kayıt, API kimliği ve oranıyla."""
    from app.models import Coupon, CouponPick, OddsQuote, Prediction

    lyon = get_or_create_team(session, "of", "Olympique Lyonnais", "club")
    lille = get_or_create_team(session, "of", "Lille OSC", "club")
    temp = Team(name="Lyon", kind="club", is_temp=True, api_id=80)
    session.add(temp)
    session.commit()
    espn_m = _finished(session, lille, lyon, 0, 1, stats={"summary": "ok"})
    session.add(Prediction(match_id=espn_m.id, kind="live", probs={"MS1": 0.4}))
    coupon = Coupon(mode="safe", source="user", target_odds=2.0, total_odds=2.0, prob=0.5, status="lost",
                    play_date=datetime(2026, 11, 21).date())
    coupon.picks = [CouponPick(match_id=espn_m.id, market="MS2", odds=2.0, prob=0.5, result="won")]
    session.add(coupon)
    api_m = _finished(session, lille, temp, 0, 1, api_fixture_id=1552820, unmatched=True)
    session.add(OddsQuote(match_id=api_m.id, bookmaker="b365", market="MS2", odds=2.1,
                          fetched_at=datetime(2026, 11, 21, 8, tzinfo=timezone.utc), source="api"))
    session.commit()
    espn_id, api_row_id = espn_m.id, api_m.id

    merge_team(session, temp.id, lyon.id)

    pair = session.query(Match).filter_by(home_team_id=lille.id, away_team_id=lyon.id).all()
    assert [m.id for m in pair] == [espn_id]
    kept = pair[0]
    assert (kept.api_fixture_id, kept.home_goals, kept.away_goals, kept.unmatched) == (1552820, 0, 1, False)
    assert kept.stats.get("summary") == "ok"
    assert session.get(Match, api_row_id) is None
    assert session.query(OddsQuote).filter_by(match_id=espn_id).count() == 1
    assert session.query(Prediction).filter_by(match_id=espn_id).count() == 1
    assert session.query(CouponPick).filter_by(match_id=espn_id).count() == 1


def test_merge_team_keeps_finished_pair_with_different_scores(session):
    """Skorlar farklıysa hangisinin doğru olduğu bilinmez: iki kayda da dokunulmaz."""
    lyon = get_or_create_team(session, "of", "Olympique Lyonnais", "club")
    lille = get_or_create_team(session, "of", "Lille OSC", "club")
    temp = Team(name="Lyon", kind="club", is_temp=True)
    session.add(temp)
    session.commit()
    _finished(session, lille, lyon, 0, 1)
    _finished(session, lille, temp, 2, 1, api_fixture_id=1552820, unmatched=True)

    merge_team(session, temp.id, lyon.id)

    assert session.query(Match).filter_by(home_team_id=lille.id, away_team_id=lyon.id).count() == 2


def test_merge_team_drops_placeholder_predictions_of_unmatched_copy(session):
    """Eşleşmemiş kopyanın tahminleri boş yer tutucudur ("eşleşmedi"); asıl tahminle aynı saatte yazılmış olabilir.
    Taşınsalardı maç sayfası son tahmin olarak boş olanı gösterirdi (21 Kas 2026 bulut verisi)."""
    from app.model.predict import latest_prediction
    from app.models import Prediction

    lyon = get_or_create_team(session, "of", "Olympique Lyonnais", "club")
    lille = get_or_create_team(session, "of", "Lille OSC", "club")
    temp = Team(name="Lyon", kind="club", is_temp=True)
    session.add(temp)
    session.commit()
    at = datetime(2026, 11, 21, 6, 11, tzinfo=timezone.utc)
    espn_m = _finished(session, lille, lyon, 0, 1, stats={"summary": "ok"})
    session.add(Prediction(match_id=espn_m.id, kind="live", created_at=at, lam_home=1.38, lam_away=1.2,
                           probs={"MS1": 0.4}))
    session.commit()
    api_m = _finished(session, lille, temp, 0, 1, api_fixture_id=1552820, unmatched=True)
    session.add(Prediction(match_id=api_m.id, kind="live", created_at=at, probs={}, flags=["eşleşmedi"]))
    session.commit()

    merge_team(session, temp.id, lyon.id)

    assert latest_prediction(session, espn_m.id).probs == {"MS1": 0.4}
    assert session.query(Prediction).filter_by(match_id=espn_m.id).count() == 1
