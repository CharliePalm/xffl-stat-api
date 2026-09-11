from sqlalchemy.orm import Mapped

from shared.model import PlayerStatline
from shared.service.player_week_service import PlayerWeekDataColumns
from shared.service.service import filters_model
from shared.service.sql_model import BaseSQLModel
from shared.service.view_service import ViewService


class PlayerStatlineModel(PlayerWeekDataColumns, BaseSQLModel):
    __tablename__ = "player_statline"

    first_name: Mapped[str]
    last_name: Mapped[str]
    team: Mapped[str]
    position: Mapped[str]


PlayerStatlineFilters = filters_model(PlayerStatline, name="PlayerStatlineFilters")


class PlayerStatlineService(ViewService[PlayerStatlineModel, PlayerStatline]):
    schema = PlayerStatline
    row = PlayerStatlineModel
