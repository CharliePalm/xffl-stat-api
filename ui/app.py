import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, render_template, request

from shared.model import NFLPosition, NFLTeam

from api_client import ApiError, get_player_statlines

app = Flask(__name__)

TEAMS = sorted(str(team) for team in NFLTeam)
POSITIONS = [position.value for position in NFLPosition]


def _team_abbreviation(team) -> str:
    # The API serializes `team` as [full_name, abbreviation, team_id] (or None).
    if isinstance(team, list):
        return team[1]
    return team or ""


STAT_CATEGORIES = [
    ("passing_yards", "Passing Yards"),
    ("passing_tds", "Passing TDs"),
    ("interceptions_thrown", "Interceptions Thrown"),
    ("rushing_yards", "Rushing Yards"),
    ("rushing_tds", "Rushing TDs"),
    ("receptions", "Receptions"),
    ("receiving_yards", "Receiving Yards"),
    ("receiving_tds", "Receiving TDs"),
    ("fumbles_lost", "Fumbles Lost"),
    ("two_pt_conversions", "2pt Conversions"),
    ("field_goals_made", "Field Goals Made"),
    ("num_field_goals_missed", "Field Goals Missed"),
    ("extra_points_points_made", "Extra Points Made"),
    ("sacks", "Sacks"),
    ("interceptions", "Interceptions"),
    ("fumbles_recovered", "Fumbles Recovered"),
    ("safeties", "Safeties"),
    ("defensive_tds", "Defensive TDs"),
    ("blocked_kicks", "Blocked Kicks"),
    ("points_allowed", "Points Allowed"),
]


def _stat_categories(entry: dict[str, Any]) -> list[dict[str, Any]]:
    stats = []
    for key, label in STAT_CATEGORIES:
        value = entry.get(key)
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value) if value else "-"
        stats.append({"label": label, "value": value})
    return stats


@app.route("/", methods=["GET"])
def index():
    team = request.args.get("team") or ""
    position = request.args.get("position") or ""
    sort = request.args.get("sort", "points_desc")

    error = None
    rows: list[dict] = []
    try:
        statlines = get_player_statlines(team=team or None, position=position or None)
        rows = [
            {
                "player_name": f"{entry['first_name']} {entry['last_name']}",
                "position": entry["position"],
                "team": _team_abbreviation(entry["team"]),
                "week": entry["week"],
                "points": entry["points"],
                "stats": _stat_categories(entry),
            }
            for entry in statlines
        ]
        rows.sort(key=lambda row: row["points"], reverse=sort != "points_asc")
    except ApiError as exc:
        error = str(exc)

    return render_template(
        "index.html",
        rows=rows,
        teams=TEAMS,
        positions=POSITIONS,
        selected_team=team,
        selected_position=position,
        sort=sort,
        error=error,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5050)
