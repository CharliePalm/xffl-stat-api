from sqlalchemy.orm import Mapped, mapped_column

from shared.model import Game, NFLTeam
from shared.service.service import Criterion, Filter, Service, any_of, filters_model
from shared.service.sql_model import BaseSQLModel


class GameModel(BaseSQLModel):
    __tablename__ = "game"

    week: Mapped[int] = mapped_column(primary_key=True)
    home: Mapped[str] = mapped_column(primary_key=True)
    away: Mapped[str] = mapped_column(primary_key=True)
    cbs_link: Mapped[str]
    espn_link: Mapped[str]
    date_time: Mapped[str] = mapped_column(index=True)
    pff_id: Mapped[int]
    neutral_site: Mapped[bool]
    season: Mapped[str]
    in_progress: Mapped[bool]


GameFilters = filters_model(Game, name="GameFilters")


class GameService(Service[GameModel, Game]):
    row = GameModel
    schema = Game

    def played_in(self, team: NFLTeam) -> Filter:
        """Either side of the fixture.

        Not a `_criterion_for` case: the fixture stores `home` and
        `away`, so there is no `team` column for a filter model to carry.
        Pass the result to `search_partial` as an extra criterion.
        """
        abbreviation = str(team)
        return any_of(
            Criterion.eq("home", abbreviation),
            Criterion.eq("away", abbreviation),
        )
