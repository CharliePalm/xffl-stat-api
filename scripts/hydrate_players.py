import json
import sys
from pathlib import Path
from typing import Any

from curl_cffi import requests

from shared.model import NFLPosition, NFLTeam, Player
from shared.service.engine import SessionLocal
from shared.service.player_service import PlayerService
from shared.utils import clean_name

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}
SLEEPER_KEYS = ["first_name", "last_name", "team", "number", "active", "position"]

# tank01's player list carries cross-referenced IDs for most other sources; it
# only shares sleeperBotID with the sleeper roster above, so that's the join key
TANK01_PLAYERS_PATH = (
    Path(__file__).resolve().parent.parent
    / "scrapers"
    / "test_html"
    / "tank01_players.json"
)
# our field name -> tank01's field name
TANK01_ID_COLUMNS = {
    "tank_id": "playerID",
    "espn_id": "espnID",
    "yahoo_id": "yahooPlayerID",
    "cbs_id": "cbsPlayerID",
    "fantasy_pros_id": "fantasyProsPlayerID",
    "f_ref_id": "fRefID",
    "roto_wire_id": "rotoWirePlayerID",
}


def load_tank01_ids_by_sleeper_id() -> dict[str, dict[str, Any]]:
    with open(TANK01_PLAYERS_PATH) as fp:
        records = json.load(fp)
    return {
        record["sleeperBotID"]: record
        for record in records
        if record.get("sleeperBotID")
    }


def fetch_players() -> list[Player]:
    players = requests.get(
        "https://api.sleeper.app/players/nfl?exclude_injury=false"
    ).json()
    tank01_by_sleeper_id = load_tank01_ids_by_sleeper_id()

    built: list[Player] = []
    for player_id, raw in players.items():
        positions = [
            p for p in (raw["fantasy_positions"] or []) if p in FANTASY_POSITIONS
        ]
        if len(positions) > 1:
            if raw["active"]:
                raise ValueError(f"{player_id} has multiple fantasy positions")
            continue
        if len(positions) != 1:
            continue

        record = {k: raw[k] for k in SLEEPER_KEYS if k in raw}
        tank01_record = tank01_by_sleeper_id.get(player_id, {})
        built.append(
            Player(
                id=int(record.get("player_id") or player_id),
                first_name=record.get("first_name", ""),
                last_name=record.get("last_name", ""),
                team=record.get("team"),
                number=record.get("number"),
                active=bool(record.get("active", False)),
                position=positions[0],
                first_name_norm=clean_name(record.get("first_name", "")),
                last_name_norm=clean_name(record.get("last_name", "")),
                **{
                    field: tank01_record.get(tank01_field)
                    for field, tank01_field in TANK01_ID_COLUMNS.items()
                },
            )
        )
    return built


def build_defenses() -> list[Player]:
    return [
        Player(
            id=team.player_defense_id,
            first_name=team.team_city,
            last_name=team.team_name,
            team=team,
            number=100,
            active=True,
            position=NFLPosition.D,
            first_name_norm=clean_name(team.team_city),
            last_name_norm=clean_name(team.team_name),
        )
        for team in NFLTeam
    ]


def _row(player: Player) -> dict[str, Any]:
    """`PlayerModel`'s mapped columns are plain strings, so a bare `NFLTeam`
    (not itself a `str`, unlike `NFLPosition`) needs coercing before `put`
    hands it to SQLAlchemy."""
    values = player.model_dump()
    if values["team"] is not None:
        values["team"] = str(player.team)
    return values


def hydrate(players: list[Player], service: PlayerService) -> None:
    for player in players:
        service.put(None, _row(player))


def main() -> None:
    players = fetch_players()
    if not isinstance(players, list):
        print("Expected a JSON array of player objects.")
        sys.exit(1)

    session = SessionLocal()
    service = PlayerService(session)
    with session.begin():
        hydrate(players, service)
        print("hydrated ", len(players), " players")
        hydrate(build_defenses(), service)


if __name__ == "__main__":
    main()
