import os
from typing import Any

import requests

API_BASE_URL = os.environ.get("XFFL_API_BASE_URL", "http://localhost:6969/api/v1")
API_KEY = os.environ.get("XFFL_API_KEY", "")

MAX_PAGE_SIZE = 200


class ApiError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _fetch_all(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Page through an XFFL API list endpoint and return every matching row."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = {**params, "limit": MAX_PAGE_SIZE, "offset": offset}
        response = requests.get(
            f"{API_BASE_URL}{path}", headers=_headers(), params=query, timeout=10
        )
        if not response.ok:
            raise ApiError(f"{path} returned {response.status_code}: {response.text}")
        page = response.json()
        rows.extend(page)
        if len(page) < MAX_PAGE_SIZE:
            return rows
        offset += MAX_PAGE_SIZE


def get_player_statlines(
    team: str | None = None, position: str | None = None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if team:
        params["team"] = team
    if position:
        params["position"] = position
    return _fetch_all("/player-statlines", params)
