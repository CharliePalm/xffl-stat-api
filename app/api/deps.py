from collections.abc import Callable
from typing import Annotated, Any, Optional, cast

from fastapi import Depends
from sqlalchemy.orm import Session

from shared.model import NFLTeam
from shared.service.engine import get_session
from shared.service.game_service import GameService
from shared.service.player_service import PlayerService
from shared.service.player_week_service import PlayerWeekService
from shared.service.provider import ProviderService
from shared.service.service import Page, Service

SessionDep = Annotated[Session, Depends(get_session)]


def get_page(limit: int = 50, offset: int = 0) -> Page:
    """`?limit=&offset=` as a Page."""
    return Page(limit=limit, offset=offset)


def get_team(team: Optional[NFLTeam] = None) -> Optional[NFLTeam]:
    """`?team=BUF`, validated into an NFLTeam."""
    return team


# Both go through Depends rather than being declared on the route
# directly. A route that binds a Pydantic model to the query string
# (`Annotated[SomeFilters, Query()]`) stops exploding that model into
# individual params as soon as it has a plain query param beside it —
# the model then reads as one required param called "filters". Params
# resolved as dependencies do not collide with it.
PageDep = Annotated[Page, Depends(get_page)]
TeamDep = Annotated[Optional[NFLTeam], Depends(get_team)]

# One entry per stat entity. Adding a new one is a single line here, no
# new get_*_service function required.
_SERVICE_TYPES: dict[str, type[Service[Any, Any]]] = {
    "game": GameService,
    "player": PlayerService,
    "player_week": PlayerWeekService,
    "provider": ProviderService,
}


def service_factory(
    session: SessionDep,
) -> dict[str, Callable[[], Service[Any, Any]]]:
    """
    Entity name -> lambda that builds (and caches) that entity's service
    for this request. Every dependency asking for the same name within
    one request gets back the same instance; a fresh cache is built per
    request since `session` itself is request-scoped.
    """
    cache: dict[str, Service[Any, Any]] = {}
    return {
        name: (lambda name=name, cls=cls: cache.setdefault(name, cls(session)))
        for name, cls in _SERVICE_TYPES.items()
    }


ServiceFactoryDep = Annotated[
    dict[str, Callable[[], Service[Any, Any]]], Depends(service_factory)
]


def get_game_service(factory: ServiceFactoryDep) -> GameService:
    return cast(GameService, factory["game"]())


def get_player_service(factory: ServiceFactoryDep) -> PlayerService:
    return cast(PlayerService, factory["player"]())


def get_player_week_service(factory: ServiceFactoryDep) -> PlayerWeekService:
    return cast(PlayerWeekService, factory["player_week"]())


def get_provider_service(factory: ServiceFactoryDep) -> ProviderService:
    return cast(ProviderService, factory["provider"]())


GameServiceDep = Annotated[GameService, Depends(get_game_service)]
PlayerServiceDep = Annotated[PlayerService, Depends(get_player_service)]
PlayerWeekServiceDep = Annotated[PlayerWeekService, Depends(get_player_week_service)]
ProviderServiceDep = Annotated[ProviderService, Depends(get_provider_service)]
