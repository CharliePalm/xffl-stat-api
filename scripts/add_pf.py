#!/usr/bin/env python3
"""
Backfill xffl.db `games` rows with PFF's game id (stored as `pff_id`).

Usage:
    python update_pff_ids.py --schedule schedule.json --db ./db/xffl.db [--season 2026]

Reads a PFF `/api/scoreboard/schedules`-style payload (weeks -> games),
matches each game to an existing row in `games` by (season, week, home,
away), and sets pff_id. Idempotent — safe to re-run as new weeks post.
"""

import argparse
import json
import sqlite3

from scrapers.pff import PFF_ABBR_FIXES

DB = "../../srv/infra/docker/db/xffl.db"


def normalize(abbr: str) -> str:
    return PFF_ABBR_FIXES.get(abbr, abbr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    args = ap.parse_args()

    with open("scripts/pff_schedule.json") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(games)")
    cols = {row[1] for row in cur.fetchall()}
    if "pff_id" not in cols:
        cur.execute("ALTER TABLE games ADD COLUMN pff_id INTEGER")

    updated, unmatched = 0, []
    for week in data["weeks"]:
        week_id = week["id"]  # e.g. -2 for "P1", 1 for "1"
        for g in week["games"]:
            home = normalize(g["home_franchise"]["abbreviation"])
            away = normalize(g["away_franchise"]["abbreviation"])
            pff_id = g["pff_game_id"]

            cur.execute(
                """UPDATE games SET pff_id = ?
                   WHERE season = ? AND week = ? AND home = ? AND away = ? and pff_id is null""",
                (pff_id, args.season, week_id, home, away),
            )
            if cur.rowcount:

                updated += cur.rowcount
            else:
                unmatched.append((week_id, away, home, pff_id))

    conn.commit()
    conn.close()

    print(f"Updated {updated} rows.")
    if unmatched:
        print(f"No match for {len(unmatched)} games (week, away, home, pff_id):")
        for row in unmatched:
            print(f"  {row}")


if __name__ == "__main__":
    main()
