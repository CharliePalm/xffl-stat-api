from typing import Any, ClassVar, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Mapped, mapped_column

from shared.model import NFLTeam, Player
from shared.service.service import Criterion, Filter, Service, filters_model
from shared.service.sql_model import BaseSQLModel
from shared.utils import clean_name
from shared.exceptions import DataIntegrityException


class PlayerModel(BaseSQLModel):
    __tablename__ = "player"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str]
    last_name: Mapped[str]
    team: Mapped[Optional[str]] = mapped_column(default=None)
    number: Mapped[Optional[int]] = mapped_column(default=None)
    active: Mapped[bool]
    position: Mapped[str]
    first_name_norm: Mapped[str]
    last_name_norm: Mapped[str]
    tank_id: Mapped[Optional[str]] = mapped_column(default=None)
    espn_id: Mapped[Optional[str]] = mapped_column(default=None)
    yahoo_id: Mapped[Optional[str]] = mapped_column(default=None)
    cbs_id: Mapped[Optional[str]] = mapped_column(default=None)
    fantasy_pros_id: Mapped[Optional[str]] = mapped_column(default=None)
    f_ref_id: Mapped[Optional[str]] = mapped_column(default=None)
    roto_wire_id: Mapped[Optional[str]] = mapped_column(default=None)


#: The normalised columns back `first_name`/`last_name` searches, so they
#: are not filterable in their own right.
PlayerFilters: type[BaseModel] = filters_model(
    Player,
    exclude={"first_name_norm", "last_name_norm"},
    name="PlayerFilters",
)


class PlayerService(Service[PlayerModel, Player]):
    schema = Player
    row = PlayerModel

    #: Name input -> the normalised column it matches against, so
    #: "D'Andre" and "dandre" find the same player.
    NORMALISED_NAMES: ClassVar[dict[str, str]] = {
        "first_name": "first_name_norm",
        "last_name": "last_name_norm",
    }

    def _criterion_for(self, field: str, value: Any) -> Filter:
        column = self.NORMALISED_NAMES.get(field)
        if column is None:
            return super()._criterion_for(field, value)
        return Criterion.like(column, f"%{clean_name(str(value))}%")

    def get_by_name(
        self, first_name: str, last_name: str, team: str | NFLTeam
    ) -> Optional[Player]:
        res = self.search(
            Criterion.eq("first_name_norm", clean_name(first_name))
            & Criterion.eq("first_name_norm", clean_name(last_name))
            & Criterion.eq("team", str(team))
        )
        if len(res.items) != 1:
            raise DataIntegrityException("get_by_name bad response: " + str(res.items))
        return res.items[0]

    def get_by_full_name(self, full_name: str, team: str | NFLTeam) -> Optional[Player]:
        parts = full_name.strip().split()
        if not parts:
            return None
        first_name = parts[0]
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        return self.get_by_name(first_name, last_name, team)
