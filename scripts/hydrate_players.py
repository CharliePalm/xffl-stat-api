from curl_cffi import requests

from shared.db import Database
from shared.model import NFLTeam, Player, NFLPosition

fantasy_positions = set(["QB", "RB", "WR", "TE", "K", "DST"])
players_resp = requests.get("https://api.sleeper.app/players/nfl?exclude_injury=false")
players = players_resp.json()
filtered_players = []
keys_to_pick = ["first_name", "last_name", "team", "number", "active", "position"]
db = Database()
for player in players:
    players[player]["fantasy_positions"] = list(
        filter(
            lambda p: p in fantasy_positions, players[player]["fantasy_positions"] or []
        )
    )

    if len(players[player]["fantasy_positions"] or []) > 1:
        if players[player]["active"]:
            raise
        continue
    elif len(players[player]["fantasy_positions"] or []) == 1:
        players[player]["position"] = players[player]["fantasy_positions"][0]
        players[player] = {
            k: players[player][k] for k in keys_to_pick if k in players[player]
        }

        # Build a Pydantic Player model and persist it via Database.write_model
        record = players[player]
        player_model = Player(
            id=int(record.get("player_id") or player),
            first_name=record.get("first_name", ""),
            last_name=record.get("last_name", ""),
            team=record.get("team"),
            number=record.get("number"),
            active=bool(record.get("active", False)),
            position=record.get("position"),
        )
        filtered_players.append(player_model)


## pt 2

import sys

# Column name -> SQL type, based on the sample record's fields.
COLUMNS = {
    "id": "INT",
    "first_name": "TEXT",
    "last_name": "TEXT",
    "team": "TEXT",
    "number": "INTEGER",
    "active": "INTEGER",  # SQLite has no native BOOLEAN; stored as 0/1
    "position": "TEXT",
}
PRIMARY_KEY = ("id",)
col_names = list(COLUMNS.keys())
placeholders = ", ".join("?" for _ in col_names)
col_list = ", ".join(f'"{c}"' for c in col_names)

conflict_cols = ", ".join(f'"{c}"' for c in PRIMARY_KEY)

update_clause = ", ".join(
    f'"{c}" = excluded."{c}"' for c in col_names if c not in PRIMARY_KEY
)
sql = f"""
    INSERT INTO player ({col_list})
    VALUES ({placeholders})
    ON CONFLICT({conflict_cols}) DO UPDATE SET
        {update_clause}
"""


def hydrate(players: list[dict]) -> None:
    clean = lambda col, x: str(x) if col == "team" else x
    rows = [
        tuple(clean(col, player.__getattribute__(col)) for col in col_names)
        for player in players
    ]
    db.executemany(sql, rows)


def hydrate_defense():
    rows = [
        (
            team.id,
            team.team_city,
            team.team_name,
            team.abbreviation,
            100,
            True,
            NFLPosition.D,
        )
        for team in NFLTeam
    ]
    db.executemany(sql, rows)


def main() -> None:

    if not isinstance(filtered_players, list):
        print("Expected a JSON array of player objects.")
        sys.exit(1)

    hydrate(filtered_players)
    print("hydrated ", len(filtered_players), " players")
    hydrate_defense()
    db.commit()


if __name__ == "__main__":
    main()
