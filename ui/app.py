import sys
from pathlib import Path

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
