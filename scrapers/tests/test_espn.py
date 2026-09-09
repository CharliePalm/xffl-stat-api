from scrapers.espn import ESPNScraper
from scrapers.tests.conftest import TEST_HTML
from shared.model import NFLPosition, NFLTeam

FIXTURE = TEST_HTML / "epsn.html"
PLAY_BY_PLAY_FIXTURE = TEST_HTML / "espn_playbyplay.html"

KICKERS = {"Eddy Pineiro", "Cameron Dicker"}


def _position(name: str) -> NFLPosition:
    return NFLPosition.K if name in KICKERS else NFLPosition.WR


def test_espn_scrape(tmp_path, monkeypatch, fake_players, sf_vs_lac):
    # ESPNScraper.parse_html dumps a debug copy of the page to "./epsn.html"
    monkeypatch.chdir(tmp_path)

    scraper = ESPNScraper()
    monkeypatch.setattr(
        scraper.player_service,
        "get_by_full_name",
        lambda name, team: fake_players.by_name(name, team, _position(name)),
    )
    # the two-point conversion only lives on the separate play-by-play
    # page; avoid a real network call for it
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

    sf_defense = by_id[NFLTeam.SAN_FRANCISCO_49ERS.player_defense_id]
    lac_defense = by_id[NFLTeam.LOS_ANGELES_CHARGERS.player_defense_id]
    assert sf_defense.points_allowed == 17
    assert lac_defense.points_allowed == 41
    assert sf_defense.sacks == 1
    assert lac_defense.interceptions == 2
    # regression check: LAC's interception-return TD used to get counted
    # twice — once from the "Defense" section's own TD total (which
    # already covers every defensive/special-teams TD), and again from
    # "Interceptions"' separate TD column for the same play
    assert sf_defense.defensive_tds == 1
    assert lac_defense.defensive_tds == 1

    # regression check: SF's own "Fumbles" section total (2) counted
    # Adrian Martinez recovering his *own* team's fumble alongside Khadarel
    # Hodge's actual takeaway of LAC's; the real count is LAC's own
    # fumbles_lost (1, from Devonte Ross), not SF's section total
    assert sf_defense.fumbles_recovered == 1
    assert lac_defense.fumbles_recovered == 0

    # the two-point conversion buried in the play-by-play page's playText:
    # "DJ Uiagalelei Pass to Evan Svoboda for Two-Point Conversion"
    uiagalelei = fake_players.by_name("DJ Uiagalelei", NFLTeam.LOS_ANGELES_CHARGERS)
    assert by_id[uiagalelei.id].two_pt_conversions == 1

    svoboda = fake_players.by_name("Evan Svoboda", NFLTeam.LOS_ANGELES_CHARGERS)
    assert by_id[svoboda.id].two_pt_conversions == 1
    assert by_id[svoboda.id].points == 2.0


def test_espn_missing_player_is_skipped(tmp_path, monkeypatch, sf_vs_lac):
    monkeypatch.chdir(tmp_path)

    scraper = ESPNScraper()
    monkeypatch.setattr(scraper.player_service, "get_by_full_name", lambda name, team: None)
    monkeypatch.setattr(
        scraper, "get_play_by_play_html", lambda game: PLAY_BY_PLAY_FIXTURE.read_text()
    )

    soup = scraper.parse_html(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    assert result.player_week_data
    assert all(row.player_id < 0 for row in result.player_week_data)
