import json

from scrapers.pff import PFFScraper
from scrapers.tests.conftest import TEST_HTML
from shared.model import NFLPosition, NFLTeam

FIXTURE = TEST_HTML / "pff.json"

# PFF spells these with the site's own accents/capitalization
KICKERS = {"Eddy Piñeiro", "Cameron Dicker"}


def _position(name: str) -> NFLPosition:
    return NFLPosition.K if name in KICKERS else NFLPosition.WR


def test_pff_scrape(tmp_path, monkeypatch, fake_players, sf_vs_lac):
    # PFFScraper.scrape dumps a debug copy of the payload to "./pff.json"
    monkeypatch.chdir(tmp_path)

    scraper = PFFScraper()
    monkeypatch.setattr(
        scraper.player_service,
        "get_by_full_name",
        lambda name, team: fake_players.by_name(name, team, _position(name)),
    )

    soup = json.loads(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    assert result.game == sf_vs_lac
    by_id = {row.player_id: row for row in result.player_week_data}

    mccormick = fake_players.by_name("Sincere McCormick", NFLTeam.SAN_FRANCISCO_49ERS)
    line = by_id[mccormick.id]
    assert line.rushing_yards == 53
    assert line.rushing_tds == 1

    pineiro = fake_players.by_name(
        "Eddy Piñeiro", NFLTeam.SAN_FRANCISCO_49ERS, NFLPosition.K
    )
    assert by_id[pineiro.id].points == 11.0

    dicker = fake_players.by_name(
        "Cameron Dicker", NFLTeam.LOS_ANGELES_CHARGERS, NFLPosition.K
    )
    assert by_id[dicker.id].points == 3.0

    # regression check: `scrape` used to attribute both teams' defensive
    # stats to `game.home`, silently dropping the away team's defense
    sf_defense = by_id[NFLTeam.SAN_FRANCISCO_49ERS.player_defense_id]
    lac_defense = by_id[NFLTeam.LOS_ANGELES_CHARGERS.player_defense_id]
    # each side's own D/ST touchdown doesn't count against the opposing
    # defense's points_allowed — 7 less than each team's literal final
    # score (41/17) — see `BoxscoreBuilder.DEFENSIVE_TD_POINTS`
    assert sf_defense.points_allowed == 10
    assert lac_defense.points_allowed == 34
    assert sf_defense.sacks == 1
    assert lac_defense.interceptions == 2
    assert lac_defense.defensive_tds == 1
    # these come from *_team_stats, not summed off the per-defender rows:
    # PFF's per-player `def_st_fum_rec` is 0 for every SF defender despite
    # SF's team total showing 1, and there's no per-player field for a
    # return TD at all — SF's comes from a punt return, not an INT return
    assert sf_defense.fumbles_recovered == 1
    assert sf_defense.defensive_tds == 1
    assert lac_defense.sacks == 0
    assert lac_defense.fumbles_recovered == 0

    # the two-point conversion buried in play_by_play's free text: the
    # passer already has a row from his passing line, but the receiver
    # (Svoboda) has no other PFF stat at all — every column PFF gives him
    # is zero — so his row exists purely because of this credit
    uiagalelei = fake_players.by_name("DJ Uiagalelei", NFLTeam.LOS_ANGELES_CHARGERS)
    assert by_id[uiagalelei.id].two_pt_conversions == 1

    svoboda = fake_players.by_name("Evan Svoboda", NFLTeam.LOS_ANGELES_CHARGERS)
    assert by_id[svoboda.id].two_pt_conversions == 1
    assert by_id[svoboda.id].points == 2.0


def test_pff_missing_player_is_skipped(tmp_path, monkeypatch, sf_vs_lac):
    monkeypatch.chdir(tmp_path)

    scraper = PFFScraper()
    monkeypatch.setattr(scraper.player_service, "get_by_full_name", lambda name, team: None)

    soup = json.loads(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    assert result.player_week_data
    assert all(row.player_id < 0 for row in result.player_week_data)


def test_pff_no_stats_returns_empty(sf_vs_lac):
    scraper = PFFScraper()
    result = scraper.scrape({}, sf_vs_lac)
    assert result.player_week_data == []
    assert result.game == sf_vs_lac
