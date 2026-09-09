"""Cross-scraper consistency check.

cbs.html, epsn.html, pff.json, tank01.json and sleeper.html all describe
the same game (49ers @ Chargers, final 41-17). In principle every source
should produce the same `ScrapedPageInfo` for it — same players, same
stat lines, same defenses. This test scrapes all five and asserts they
agree.

It is expected to fail today. Known disagreements:

- Name spelling: PFF's "Eddy Piñeiro" vs. everyone else's "Eddy Pineiro" —
  `clean_name` drops the accented character rather than transliterating
  it, so the two land under different identities entirely.
- CBS double-counts some defensive stats: its per-defender rows and its
  team-summary table both add to interceptions/fumbles_recovered/
  defensive_tds for the same game, so its totals run higher than
  ESPN/PFF/tank01's.
- Sleeper reports more players than the other four (63 vs. 42) and its
  defensive `pts_allow` stat doesn't line up with the literal final score
  the other sources use (e.g. it has SF's defense "allowing" 11 rather
  than LAC's actual 17 points) — a scoring-bucket value, not the score.

Kept here so a future fix to any of these has a test that goes green.
"""

import json

import pytest

from scrapers.cbs import CBSScraper
from scrapers.espn import ESPNScraper
from scrapers.pff import PFFScraper
from scrapers.sleeper import SleeperScraper
from scrapers.tank01 import TankScraper
from scrapers.tests.conftest import TEST_HTML, FakePlayers
from shared.model import NFLPosition, NFLTeam, PlayerWeekData

# names both PFF and the other three sources report as kickers, spelling
# included — position (not raw stats) decides the K vs. offense scoring
# branch in BoxscoreBuilder, so every source needs to agree on it for a
# fair comparison
KICKER_NAMES = {"Eddy Pineiro", "Eddy Piñeiro", "Cameron Dicker"}


def _position(name: str) -> NFLPosition:
    return NFLPosition.K if name in KICKER_NAMES else NFLPosition.WR


def _scrape_cbs(fake_players, game):
    scraper = CBSScraper()
    scraper.player_service.get_by_full_name = lambda name, team: fake_players.by_name(
        name, team, _position(name)
    )
    # CBS's two-point-conversion credit only has a jersey number + first
    # initial + last name to go on, never a full name — this stands in
    # for get_by_name's real last_name+team fallback
    full_name_by_last_name = {"Uiagalelei": "DJ Uiagalelei", "Svoboda": "Evan Svoboda"}
    scraper.player_service.get_by_name = lambda first, last, team: fake_players.by_name(
        full_name_by_last_name[last], team, _position(full_name_by_last_name[last])
    )
    # the lost-fumble credit only has a team + jersey number to go on,
    # from the separate play-by-play page
    full_name_by_number = {("LAC", 24): "Devonte Ross"}
    scraper.player_service.get_by_number = lambda number, team: fake_players.by_name(
        full_name_by_number[(team.abbreviation, number)], team
    )
    scraper.get_play_by_play_html = lambda game: (
        TEST_HTML / "cbs_playbyplay.html"
    ).read_text()
    soup = scraper.parse_html((TEST_HTML / "cbs.html").read_text())
    return scraper.scrape(soup, game)


def _scrape_espn(fake_players, game):
    scraper = ESPNScraper()
    scraper.player_service.get_by_full_name = lambda name, team: fake_players.by_name(
        name, team, _position(name)
    )
    # the two-point conversion only lives on the separate play-by-play
    # page; avoid a real network call for it
    scraper.get_play_by_play_html = lambda game: (
        TEST_HTML / "espn_playbyplay.html"
    ).read_text()
    soup = scraper.parse_html((TEST_HTML / "epsn.html").read_text())
    return scraper.scrape(soup, game)


def _scrape_pff(fake_players, game):
    scraper = PFFScraper()
    scraper.player_service.get_by_full_name = lambda name, team: fake_players.by_name(
        name, team, _position(name)
    )
    soup = json.loads((TEST_HTML / "pff.json").read_text())
    return scraper.scrape(soup, game)


def _scrape_tank01(fake_players, game):
    from types import SimpleNamespace

    scraper = TankScraper()
    soup = scraper.parse_html((TEST_HTML / "tank01.json").read_text())
    # tank01 looks players up by its own id, not by name, so route that
    # lookup through the same name-keyed registry the other three sources
    # use, via the id -> name/team mapping tank01's own payload carries
    name_by_tank_id = {
        player_id: (record["longName"], record["team"])
        for player_id, record in soup["playerStats"].items()
    }

    def fake_search(*criteria, **kwargs):
        tank_id = criteria[0].value
        name, team = name_by_tank_id[tank_id]
        return SimpleNamespace(
            items=[fake_players.by_name(name, team, _position(name))]
        )

    scraper.player_service.search = fake_search
    # tank01's two-point-conversion credit is looked up by name (it gives
    # no id at all for a conversion's participants), so this needs the
    # same name-keyed registry `fake_search` already routes through
    scraper.player_service.get_by_full_name = lambda name, team: fake_players.by_name(
        name, team, _position(name)
    )
    return scraper.scrape(soup, game)


def _label(player_id: int, fake_players: FakePlayers) -> str:
    """Human-readable row label: a team abbreviation for a defense's
    (negative) synthetic id, otherwise the player's name."""
    if player_id < 0:
        for team in NFLTeam:
            if team.player_defense_id == player_id:
                return f"{team.abbreviation} D/ST"
    return fake_players.name(player_id)


def _print_points_grid(
    by_source: dict[str, dict[int, PlayerWeekData]], fake_players: FakePlayers
) -> None:
    """Player/defense (rows) x provider (columns) fantasy-points grid,
    for eyeballing where sources agree or disagree at a glance."""
    providers = list(by_source.keys())
    all_ids = {pid for rows in by_source.values() for pid in rows}
    # skip anyone every provider agrees scored nothing (or didn't report at
    # all) — mostly inactive/rostered-but-unused players, not worth a row
    all_ids = {
        pid
        for pid in all_ids
        if any((row := by_source[p].get(pid)) and row.points for p in providers)
    }
    labels = {pid: _label(pid, fake_players) for pid in all_ids}
    ordered_ids = sorted(all_ids, key=lambda pid: labels[pid].lower())

    name_width = max((len(labels[pid]) for pid in ordered_ids), default=4)
    col_width = max(8, max((len(p) for p in providers), default=0) + 2)

    header = "PLAYER".ljust(name_width + 2) + "".join(
        p.rjust(col_width) for p in providers
    )
    print()
    print(header)
    print("-" * len(header))
    for pid in ordered_ids:
        cells = "".join(
            (f"{row.points:.1f}" if (row := by_source[p].get(pid)) else "-").rjust(
                col_width
            )
            for p in providers
        )
        print(labels[pid].ljust(name_width + 2) + cells)
    print()


def _scrape_sleeper(fake_players, game):
    scraper = SleeperScraper()
    scraper.player_service.get_by_name = lambda first, last, team: fake_players.by_name(
        f"{first} {last}", team, _position(f"{first} {last}")
    )
    soup = scraper.parse_html((TEST_HTML / "sleeper.html").read_text())
    return scraper.scrape(soup, game)


# @pytest.mark.xfail(
#     reason=(
#         "sources disagree on name spelling (accents), CBS double-counts "
#         "some defensive stats, and sleeper's player set/pts_allow semantics "
#         "diverge from the other four — see module docstring"
#     ),
#     strict=False,
# )
def test_all_scrapers_agree_on_the_same_game(fake_players, sf_vs_lac):
    results = {
        "cbs": _scrape_cbs(fake_players, sf_vs_lac),
        "espn": _scrape_espn(fake_players, sf_vs_lac),
        "pff": _scrape_pff(fake_players, sf_vs_lac),
        "tank01": _scrape_tank01(fake_players, sf_vs_lac),
        "sleeper": _scrape_sleeper(fake_players, sf_vs_lac),
    }

    by_source: dict[str, dict[int, PlayerWeekData]] = {
        source: {row.player_id: row for row in page.player_week_data}
        for source, page in results.items()
    }
    _print_points_grid(by_source, fake_players)

    baseline_name, baseline = next(iter(by_source.items()))

    for source, rows in by_source.items():
        if source == baseline_name:
            continue
        assert rows == baseline, f"{source} disagrees with {baseline_name}"
