from types import SimpleNamespace

from bs4 import BeautifulSoup

from scrapers.cbs import CBSScraper
from scrapers.tests.conftest import TEST_HTML
from shared.boxscore import BoxscoreBuilder, Stat
from shared.exceptions import DataIntegrityException
from shared.model import NFLPosition, NFLTeam

FIXTURE = TEST_HTML / "cbs.html"
PLAY_BY_PLAY_FIXTURE = TEST_HTML / "cbs_playbyplay.html"

# CBS's per-player tables carry no position column for offensive players
# (only the defense-ctr rows do, and even those are unreliable), so the
# scraper leans entirely on `get_by_full_name` to resolve a `Player` —
# which is exactly what decides the K vs. offensive scoring branch in
# `BoxscoreBuilder`. Kickers need to come back as NFLPosition.K for the
# test to exercise that branch correctly.
KICKERS = {"Eddy Pineiro", "Cameron Dicker"}

# the two-point-conversion credit only has a jersey number + first
# initial + last name to go on ("7-D.Uiagalelei"), never a full name —
# this fake stands in for `get_by_name`'s real last_name+team fallback,
# which is what actually resolves that in production
FULL_NAME_BY_LAST_NAME = {"Uiagalelei": "DJ Uiagalelei", "Svoboda": "Evan Svoboda"}

# the lost-fumble credit only has a team + jersey number to go on, from
# the separate play-by-play page — this fake stands in for `get_by_number`
FULL_NAME_BY_NUMBER = {("LAC", 24): "Devonte Ross"}


def _position(name: str) -> NFLPosition:
    return NFLPosition.K if name in KICKERS else NFLPosition.WR


def _fake_get_by_number(fake_players):
    def get_by_number(number, team):
        name = FULL_NAME_BY_NUMBER.get((team.abbreviation, number))
        if not name:
            raise DataIntegrityException("not found")
        return fake_players.by_name(name, team)

    return get_by_number


def test_cbs_scrape(tmp_path, monkeypatch, fake_players, sf_vs_lac):
    # CBSScraper.parse_html dumps a debug copy of the page to "./cbs.html";
    # run from a scratch dir so that side effect doesn't litter the repo.
    monkeypatch.chdir(tmp_path)

    scraper = CBSScraper()
    monkeypatch.setattr(
        scraper.player_service,
        "get_by_full_name",
        lambda name, team: fake_players.by_name(name, team, _position(name)),
    )
    monkeypatch.setattr(
        scraper.player_service,
        "get_by_name",
        lambda first, last, team: fake_players.by_name(
            FULL_NAME_BY_LAST_NAME[last], team
        ),
    )
    monkeypatch.setattr(scraper.player_service, "get_by_number", _fake_get_by_number(fake_players))
    # the lost-fumble credit only lives on the separate play-by-play page;
    # avoid a real network call for it
    monkeypatch.setattr(
        scraper, "get_play_by_play_html", lambda game: PLAY_BY_PLAY_FIXTURE.read_text()
    )

    soup = scraper.parse_html(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    assert result.game == sf_vs_lac
    by_id = {row.player_id: row for row in result.player_week_data}

    mccormick = fake_players.by_name("Sincere Mccormick", NFLTeam.SAN_FRANCISCO_49ERS)
    line = by_id[mccormick.id]
    assert line.rushing_yards == 53
    assert line.rushing_tds == 1

    pineiro = fake_players.by_name(
        "Eddy Pineiro", NFLTeam.SAN_FRANCISCO_49ERS, NFLPosition.K
    )
    assert by_id[pineiro.id].points == 11.0

    dicker = fake_players.by_name(
        "Cameron Dicker", NFLTeam.LOS_ANGELES_CHARGERS, NFLPosition.K
    )
    assert by_id[dicker.id].points == 3.0

    # the two-point conversion buried in the scoring summary's free text:
    # "7-D.Uiagalelei pass to 49-E.Svoboda" — only initials, resolved via
    # last_name+team
    uiagalelei = fake_players.by_name("DJ Uiagalelei", NFLTeam.LOS_ANGELES_CHARGERS)
    assert by_id[uiagalelei.id].two_pt_conversions == 1

    svoboda = fake_players.by_name("Evan Svoboda", NFLTeam.LOS_ANGELES_CHARGERS)
    assert by_id[svoboda.id].two_pt_conversions == 1
    assert by_id[svoboda.id].points == 2.0

    # the lost fumble buried in the play-by-play page: Ross returns a
    # kickoff and fumbles it away to SF — recovered by his own team
    # wouldn't count (see test_credit_fumbles_lost_only_counts_losses)
    ross = fake_players.by_name("Devonte Ross", NFLTeam.LOS_ANGELES_CHARGERS)
    assert by_id[ross.id].fumbles_lost == 1

    sf_defense = by_id[NFLTeam.SAN_FRANCISCO_49ERS.player_defense_id]
    lac_defense = by_id[NFLTeam.LOS_ANGELES_CHARGERS.player_defense_id]
    # SF's defense allowed whatever LAC scored, and vice versa, minus 7
    # for each side's own D/ST touchdown, which doesn't count against the
    # opposing defense — see `BoxscoreBuilder.DEFENSIVE_TD_POINTS`
    assert sf_defense.points_allowed == 10
    assert lac_defense.points_allowed == 34
    assert sf_defense.sacks == 1
    # regression check: LAC's interceptions/defensive_tds used to be
    # double-counted (once from defense-ctr's per-defender rows, again
    # from the team-summary table's "Int. - Returns"/"Fumbles - Lost"
    # rows, the latter of which also mis-attributed each team's *own*
    # fumble count as if it were a recovery) — both now come from
    # defense-ctr and the play-by-play/scoring-summary pages instead
    assert lac_defense.interceptions == 2
    assert lac_defense.defensive_tds == 1
    # SF recovered LAC's lost fumble (Ross's); LAC recovered none
    assert sf_defense.fumbles_recovered == 1
    assert lac_defense.fumbles_recovered == 0
    assert sf_defense.defensive_tds == 1


def _raise_not_found(*args, **kwargs):
    raise DataIntegrityException("not found")


def test_credit_fumbles_lost_only_counts_losses(monkeypatch, fake_players):
    """A separate game's play-by-play (not the SF@LAC fixture): one player
    fumbles on a botched snap and it's recovered by the other team (a real
    loss); a different player also fumbles a botched snap moments later,
    but his own team recovers it — that one must not be credited. Also
    pins down that team is resolved by checking which of the two teams
    actually has that jersey number, not by guessing from drive/possession
    context — CBS nests a drive-transition play inside the ending drive's
    own card, so that card's team is not reliable for who's on the field."""
    scraper = CBSScraper()
    roster = {
        ("MIA", 1): "Tua Tagovailoa",
        ("MIA", 16): "M Gronowski",
    }

    def get_by_number(number, team):
        name = roster.get((team.abbreviation, number))
        if not name:
            raise DataIntegrityException("not found")
        return fake_players.by_name(name, team)

    monkeypatch.setattr(scraper.player_service, "get_by_number", get_by_number)

    soup = BeautifulSoup(
        (TEST_HTML / "cbs_playbyplay_mia_atl.html").read_text(), "html.parser"
    )
    builder = BoxscoreBuilder(1)
    game = SimpleNamespace(home=NFLTeam.ATLANTA_FALCONS, away=NFLTeam.MIAMI_DOLPHINS)
    scraper._credit_fumbles_lost(builder, soup, game)

    gronowski = fake_players.by_name("M Gronowski", NFLTeam.MIAMI_DOLPHINS)
    assert builder._players[gronowski.id].stats == {Stat.fumbles_lost: 1}

    tagovailoa = fake_players.by_name("Tua Tagovailoa", NFLTeam.MIAMI_DOLPHINS)
    assert tagovailoa.id not in builder._players

    # the recovering team's defense gets credited alongside the fumbler
    assert builder._defenses[NFLTeam.ATLANTA_FALCONS] == {Stat.fumbles_recovered: 1}
    # Tagovailoa's fumble was recovered by his own team (MIA), so it's
    # not a takeaway — MIA's defense gets no credit for it
    assert NFLTeam.MIAMI_DOLPHINS not in builder._defenses


def test_cbs_missing_player_is_skipped(tmp_path, monkeypatch, sf_vs_lac):
    """A player CBS lists but the local roster doesn't recognize should be
    dropped, not raise — the scraper already guards this with `if player is
    None: continue`, and `_credit_two_point_conversion`/
    `_credit_fumbles_lost` catch `DataIntegrityException` the same way."""
    monkeypatch.chdir(tmp_path)

    scraper = CBSScraper()
    monkeypatch.setattr(scraper.player_service, "get_by_full_name", lambda name, team: None)
    monkeypatch.setattr(scraper.player_service, "get_by_name", _raise_not_found)
    monkeypatch.setattr(scraper.player_service, "get_by_number", _raise_not_found)
    monkeypatch.setattr(
        scraper, "get_play_by_play_html", lambda game: PLAY_BY_PLAY_FIXTURE.read_text()
    )

    soup = scraper.parse_html(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    # every player row got dropped, but both team defenses still score
    assert result.player_week_data
    assert all(row.player_id < 0 for row in result.player_week_data)
