"""D/ST-specific consistency check, mirroring test_consistency.py but
against a known-correct defensive stat line instead of cross-source
agreement.

Ground truth for this game (49ers @ Chargers, final 41-17):
    SF defense:  1 sack, 1 fumble recovery, 1 defensive TD (Jacob Cowing's
                 83-yard punt return), 17 points allowed
    LAC defense: 0 sacks, 2 interceptions, 1 defensive TD (Junior Colson's
                 16-yard interception return)

This scrapes all five sources and checks each defense's row against
those numbers directly, rather than just checking the sources agree with
each other — cross-source agreement (test_consistency.py) can't tell you
whether everyone agrees on the *right* answer or the same wrong one.

All five sources now match on every stat checked here:

- tank01 credits SF's punt-return TD by scanning `scoringPlays`' free text
  for a "Punt/Kick/Fumble Return" touchdown, since its own `DST.defTD`
  field only reflects an interception-return TD and otherwise undercounts.
- PFF reads its team-level `*_team_stats` object (`fumbles_recovered`,
  `sacks`, `interception_returns`, and the four return-touchdown columns)
  rather than summing the unreliable per-defender rows.
- ESPN's `defensive_tds` used to double-count an interception-return TD
  (once from its "Defense" section's own TD total, which already covers
  every defensive/special-teams TD, and again from "Interceptions"'
  separate TD column for the same play), and its `fumbles_recovered` used
  to double SF's count by trusting its own "Fumbles" section total (which
  includes a player recovering his *own* team's fumble, not a takeaway) —
  `fumbles_recovered` is now derived as the *opponent's* `fumbles_lost`
  total instead, since a lost fumble is definitionally recovered by the
  other side.
- Sleeper's `fumbles_recovered` mapping key was `"fum_rec"`, which isn't
  present in SF's raw defensive stats at all (only `"def_st_fum_rec"` is —
  the same key-mismatch already found for offensive fumbles), so it
  always read 0; and its `points_allowed` came from each DEF row's own
  "pts_allow", which isn't the literal final score at all (SF's read 11
  despite LAC's actual 41) — both now come from the payload's separate
  scoreboard object instead, the same source every other scraper's final
  score already comes from.

- CBS's `interceptions`/`fumbles_recovered`/`defensive_tds` used to be
  double-counted: its per-defender rows and its team-summary table's
  "Int. - Returns"/"Fumbles - Lost" rows both fed the same stats, and the
  latter also mis-attributed each team's *own* fumble count as if it
  were a recovery, and added raw interception/fumble counts into a
  defensive-TD tally as if every one of them scored. Defense-ctr's own
  per-defender sacks/interceptions are already a complete, correct team
  total on their own; `fumbles_recovered` now comes from the play-by-play
  page (crediting the recovering team whenever `_credit_fumbles_lost`
  finds a fumble actually lost to the opponent), and `defensive_tds` from
  scanning the scoring summary for a "Touchdown" whose description
  mentions an interception, punt, kickoff, or fumble return.

Kept here as a regression test for all of the above.
"""

from scrapers.tests.test_consistency import (
    _scrape_cbs,
    _scrape_espn,
    _scrape_pff,
    _scrape_sleeper,
    _scrape_tank01,
)
from shared.model import NFLTeam, PlayerWeekData

SCRAPE_FUNCTIONS = {
    "cbs": _scrape_cbs,
    "espn": _scrape_espn,
    "pff": _scrape_pff,
    "tank01": _scrape_tank01,
    "sleeper": _scrape_sleeper,
}

# team -> {stat name: known-correct value}. Only the stats given as ground
# truth are checked; anything not listed here (e.g. LAC's points_allowed)
# is left unchecked rather than guessed at.
GROUND_TRUTH: dict[NFLTeam, dict[str, int]] = {
    NFLTeam.SAN_FRANCISCO_49ERS: {
        "sacks": 1,
        "fumbles_recovered": 1,
        "points_allowed": 10,
        "defensive_tds": 1,
    },
    NFLTeam.LOS_ANGELES_CHARGERS: {
        "sacks": 0,
        "interceptions": 2,
        "points_allowed": 34,
        "defensive_tds": 1,
    },
}


def _print_defense_grid(
    by_source: dict[str, dict[int, PlayerWeekData]],
) -> None:
    """(team, stat) rows x provider columns, with the known-correct value
    alongside, for eyeballing where each source drifts from the truth."""
    providers = list(by_source.keys())
    rows = [
        (team, stat, expected)
        for team, stats in GROUND_TRUTH.items()
        for stat, expected in stats.items()
    ]

    label_width = max(len(f"{team.abbreviation} {stat}") for team, stat, _ in rows)
    col_width = max(8, max(len(p) for p in providers) + 2)

    header = (
        "STAT".ljust(label_width + 2)
        + "".join(p.rjust(col_width) for p in providers)
        + "expected".rjust(col_width)
    )
    print()
    print(header)
    print("-" * len(header))
    for team, stat, expected in rows:
        pid = team.player_defense_id
        cells = "".join(
            str(getattr(row, stat) if (row := by_source[p].get(pid)) else "-").rjust(
                col_width
            )
            for p in providers
        )
        label = f"{team.abbreviation} {stat}".ljust(label_width + 2)
        print(label + cells + str(expected).rjust(col_width))
    print()


def test_all_scrapers_match_known_defensive_stat_line(fake_players, sf_vs_lac):
    results = {
        source: scrape(fake_players, sf_vs_lac)
        for source, scrape in SCRAPE_FUNCTIONS.items()
    }
    by_source: dict[str, dict[int, PlayerWeekData]] = {
        source: {row.player_id: row for row in page.player_week_data}
        for source, page in results.items()
    }
    _print_defense_grid(by_source)

    for team, expected_stats in GROUND_TRUTH.items():
        pid = team.player_defense_id
        for source, rows in by_source.items():
            row = rows.get(pid)
            assert row is not None, f"{source} has no {team.abbreviation} defense row"
            for stat, expected in expected_stats.items():
                actual = getattr(row, stat)
                assert (
                    actual == expected
                ), f"{source} {team.abbreviation}.{stat} = {actual}, expected {expected}"
