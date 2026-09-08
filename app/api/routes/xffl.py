from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.deps import (
    GameServiceDep,
    PageDep,
    PlayerServiceDep,
    PlayerWeekServiceDep,
    ProviderServiceDep,
    TeamDep,
)
from shared.model import Game, Player, PlayerWeekData, Provider
from shared.service.game_service import GameFilters
from shared.service.player_service import PlayerFilters
from shared.service.player_week_service import PlayerWeekFilters
from shared.service.provider import ProviderFilters

router = APIRouter(tags=["stats"])


@router.get("/players", response_model=list[Player])
def search_players(
    players: PlayerServiceDep,
    filters: Annotated[PlayerFilters, Query()],
    page: PageDep,
) -> Any:
    """
    Search players by any Player field. Names match loosely.
    """
    return players.search_partial(filters, page=page).items


@router.get("/games", response_model=list[Game])
def search_games(
    games: GameServiceDep,
    filters: Annotated[GameFilters, Query()],
    page: PageDep,
    team: TeamDep = None,
) -> Any:
    """
    Search games by any Game field. `team` matches either side of the
    fixture, where `home` and `away` match only that side.
    """
    played = [games.played_in(team)] if team is not None else []
    return games.search_partial(filters, *played, page=page).items


@router.get("/player-week-data", response_model=list[PlayerWeekData])
def search_player_week_data(
    player_weeks: PlayerWeekServiceDep,
    filters: Annotated[PlayerWeekFilters, Query()],
    page: PageDep,
) -> Any:
    """
    Search player week stat lines by any PlayerWeekData field.
    """
    return player_weeks.search_partial(filters, page=page).items


@router.get("/providers", response_model=list[Provider])
def search_providers(
    providers: ProviderServiceDep,
    filters: Annotated[ProviderFilters, Query()],
    page: PageDep,
) -> Any:
    """
    Search providers. `name` matches loosely.
    """
    return providers.search_partial(filters, page=page).items
