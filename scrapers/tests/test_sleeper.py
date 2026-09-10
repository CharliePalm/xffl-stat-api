import json

from scrapers.sleeper import SleeperScraper
from scrapers.tests.conftest import TEST_HTML
from shared.model import NFLPosition, NFLTeam

FIXTURE = TEST_HTML / "sleeper.html"

KICKERS = {"Eddy Pineiro", "Cameron Dicker"}


def _position(name: str) -> NFLPosition:
    return NFLPosition.K if name in KICKERS else NFLPosition.WR


def _dumps(obj: object) -> str:
    # sleeper's real payload is minified; find_objects' markers rely on that
    return json.dumps(obj, separators=(",", ":"))


def _flight_html(records: list[dict]) -> str:
    """Wrap `records` the way sleeper's page wraps its flight payload:
    concatenated JSON objects, escaped into a `self.__next_f.push([id, "..."])`
    call. Mirrors what `reconstruct_flight_text` unwraps in production."""
    text = " ".join(_dumps(r) for r in records)
    chunk = json.dumps(text)[1:-1]
    return f'<script>self.__next_f.push([0,"{chunk}"])</script>'


def test_sleeper_scrape(monkeypatch, fake_players, sf_vs_lac):
    scraper = SleeperScraper()
    monkeypatch.setattr(
        scraper.player_service,
        "get_by_name",
        lambda first, last, team: fake_players.by_name(
            f"{first} {last}", team, _position(f"{first} {last}")
        ),
    )

    soup = scraper.parse_html(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    assert result.game == sf_vs_lac
    by_id = {row.player_id: row for row in result.player_week_data}

    mccormick = fake_players.by_name("Sincere McCormick", "SF")
    line = by_id[mccormick.id]
    assert line.rushing_yards == 53
    assert line.rushing_tds == 1

    pineiro = fake_players.by_name("Eddy Pineiro", "SF", NFLPosition.K)
    assert by_id[pineiro.id].points == 11.0

    dicker = fake_players.by_name("Cameron Dicker", "LAC", NFLPosition.K)
    assert by_id[dicker.id].points == 3.0

    sf_defense = by_id[NFLTeam.SAN_FRANCISCO_49ERS.player_defense_id]
    lac_defense = by_id[NFLTeam.LOS_ANGELES_CHARGERS.player_defense_id]
    assert sf_defense.sacks == 1
    assert sf_defense.defensive_tds == 1
    assert lac_defense.interceptions == 2
    # regression check: each DEF row's own "pts_allow" isn't the literal
    # final score (SF's read 11 despite LAC's actual 41) — points_allowed
    # now comes from the payload's scoreboard object instead. It's still
    # 7 below each team's literal score, though: each side's own D/ST
    # touchdown doesn't count against the opposing defense — see
    # `BoxscoreBuilder.DEFENSIVE_TD_POINTS`
    assert sf_defense.points_allowed == 10
    assert lac_defense.points_allowed == 34
    # regression check: sleeper's key here is "def_st_fum_rec", not the
    # "fum_rec" the scraper used to read (always 0 as a result)
    assert sf_defense.fumbles_recovered == 1


def test_sleeper_missing_player_is_skipped(monkeypatch, sf_vs_lac):
    scraper = SleeperScraper()
    monkeypatch.setattr(
        scraper.player_service, "get_by_name", lambda first, last, team: None
    )

    soup = scraper.parse_html(FIXTURE.read_text())
    result = scraper.scrape(soup, sf_vs_lac)

    assert result.player_week_data
    assert all(row.player_id < 0 for row in result.player_week_data)


def test_sleeper_team_duplicate_row_is_not_double_counted(monkeypatch, fake_players, sf_vs_lac):
    """Sleeper emits a defense's stat row twice: once at `position: "DEF"`
    and again, identically keyed by `player_id`, at `position: "TEAM"`.
    `_parse_player_records` must keep exactly one — this pins that down
    with a synthetic payload whose "TEAM" copy carries obviously-wrong
    stats, so silently preferring it would be caught here."""
    records = [
        {
            "category": "stat",
            "game_id": "TESTGAME",
            "player_id": "9001",
            "team": "LAC",
            "player": {"position": "DEF"},
            "stats": {"pts_allow": 14, "sack": 2, "int": 1, "fum_rec": 1, "def_st_td": 1},
        },
        {
            "category": "stat",
            "game_id": "TESTGAME",
            "player_id": "9001",
            "team": "LAC",
            "player": {"position": "TEAM"},
            "stats": {"pts_allow": 14, "sack": 99, "int": 99, "fum_rec": 99, "def_st_td": 99},
        },
    ]
    scraper = SleeperScraper()
    soup = scraper.parse_html(_flight_html(records))
    result = scraper.scrape(soup, sf_vs_lac)

    lac_defense = next(
        row
        for row in result.player_week_data
        if row.player_id == NFLTeam.LOS_ANGELES_CHARGERS.player_defense_id
    )
    assert lac_defense.sacks == 2
    assert lac_defense.interceptions == 1
