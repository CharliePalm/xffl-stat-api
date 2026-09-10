from types import SimpleNamespace

from scrapers.tank01 import TankScraper
from scrapers.tests.conftest import TEST_HTML
from shared.model import NFLPosition, NFLTeam

FIXTURE = TEST_HTML / "tank01.json"

KICKERS = {"4034949", "4362081"}  # Eddy Pineiro (SF), Cameron Dicker (LAC)


def _fake_search(fake_players):
    """Stand-in for `PlayerService.search(Criterion.eq("tank_id", ...))`."""

    def search(*criteria, **kwargs):
        tank_id = criteria[0].value
        position = NFLPosition.K if tank_id in KICKERS else NFLPosition.WR
        return SimpleNamespace(items=[fake_players.by_tank_id(tank_id, position)])

    return search


def test_tank01_parse_html_unwraps_rapidapi_envelope():
    """tank01.json is a RapidAPI Lambda-style envelope: {statusCode, body}.
    `parse_html` must hand back `body`, not the envelope itself."""
    scraper = TankScraper()
    soup = scraper.parse_html(FIXTURE.read_text())
    assert "playerStats" in soup
    assert soup["away"] == "SF"
    assert soup["home"] == "LAC"


def test_tank01_get_url_matches_fixture_game_id():
    import json

    body = json.loads(FIXTURE.read_text())["body"]
    scraper = TankScraper()
    game = SimpleNamespace(
        date_time="2026-08-20 20:00:00",
        away=NFLTeam.SAN_FRANCISCO_49ERS,
        home=NFLTeam.LOS_ANGELES_CHARGERS,
    )
    url = scraper.get_url(game)
    assert f"gameID={body['gameID']}" in url


# DJ Uiagalelei already gets a Player row from his passing/rushing lines
# (via `search`, tank_id-keyed); his two-point-conversion credit must land
# on that same row, not a second one, so route both through the same id.
_TWO_POINT_NAME_TO_TANK_ID = {"DJ Uiagalelei": "4429020"}


def _fake_get_by_full_name(fake_players):
    """Stand-in for `PlayerService.get_by_full_name`, used only for the
    two-point-conversion credit `_credit_two_point_conversion` looks up by
    name (tank01 gives no id for a conversion's participants at all)."""

    def get_by_full_name(name, team):
        tank_id = _TWO_POINT_NAME_TO_TANK_ID.get(name)
        if tank_id:
            return fake_players.by_tank_id(tank_id)
        return fake_players.by_name(name, team)

    return get_by_full_name


def test_tank01_scrape(monkeypatch, fake_players, sf_vs_lac):
    scraper = TankScraper()
    monkeypatch.setattr(scraper.player_service, "search", _fake_search(fake_players))
    monkeypatch.setattr(
        scraper.player_service, "get_by_full_name", _fake_get_by_full_name(fake_players)
    )

    soup = scraper.parse_html(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    assert result.game == sf_vs_lac
    by_id = {row.player_id: row for row in result.player_week_data}

    mccormick = fake_players.by_tank_id("4430104")
    line = by_id[mccormick.id]
    assert line.rushing_yards == 53
    assert line.rushing_tds == 1

    pineiro = fake_players.by_tank_id("4034949", NFLPosition.K)
    assert by_id[pineiro.id].points == 11.0

    dicker = fake_players.by_tank_id("4362081", NFLPosition.K)
    assert by_id[dicker.id].points == 3.0

    sf_defense = by_id[NFLTeam.SAN_FRANCISCO_49ERS.player_defense_id]
    lac_defense = by_id[NFLTeam.LOS_ANGELES_CHARGERS.player_defense_id]
    # each side's own D/ST touchdown (Colson's INT return for LAC,
    # Cowing's punt return for SF) doesn't count against the opposing
    # defense's points_allowed — 7 less than each team's literal final
    # score (41/17) — see `BoxscoreBuilder.DEFENSIVE_TD_POINTS`
    assert sf_defense.points_allowed == 10
    assert lac_defense.points_allowed == 34
    assert sf_defense.sacks == 1
    assert lac_defense.interceptions == 2
    assert lac_defense.defensive_tds == 1
    # tank01's own DST.defTD only reflects LAC's INT-return TD (Junior
    # Colson) — SF's punt-return TD ("Jacob Cowing 83 Yd Punt Return")
    # is missing from SF's defTD entirely and has to come from scanning
    # scoringPlays instead
    assert sf_defense.defensive_tds == 1
    assert sf_defense.fumbles_recovered == 1

    # the two-point conversion buried in scoringPlays' free text: both the
    # passer (already on the sheet from his passing line) and the receiver
    # (who tank01 gives no id for anywhere) get credit for it
    uiagalelei = fake_players.by_tank_id("4429020")
    assert by_id[uiagalelei.id].two_pt_conversions == 1

    svoboda = fake_players.by_name("Evan Svoboda", NFLTeam.LOS_ANGELES_CHARGERS)
    assert by_id[svoboda.id].two_pt_conversions == 1
    assert by_id[svoboda.id].points == 2.0


def test_tank01_missing_player_is_skipped_not_indexerror(monkeypatch, sf_vs_lac):
    """Regression test: `_add_player` used to index `.items[0]` straight off
    the search result, which raised IndexError for any player not in the
    local roster instead of skipping them."""
    scraper = TankScraper()
    monkeypatch.setattr(
        scraper.player_service, "search", lambda *a, **k: SimpleNamespace(items=[])
    )
    monkeypatch.setattr(
        scraper.player_service, "get_by_full_name", lambda name, team: None
    )

    soup = scraper.parse_html(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    assert result.player_week_data
    assert all(row.player_id < 0 for row in result.player_week_data)


def test_tank01_unknown_team_abbreviation_is_normalized():
    """tank01 spells some teams differently from this app's canonical
    NFLTeam abbreviation (e.g. "WSH" vs our "WAS")."""
    from scrapers.tank01 import _team

    assert _team("WSH") is NFLTeam.WASHINGTON_COMMANDERS
    assert _team("SF") is NFLTeam.SAN_FRANCISCO_49ERS
