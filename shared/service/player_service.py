from typing import Any, ClassVar, Optional

from fastapi.logger import logger
from pydantic import BaseModel
from sqlalchemy.orm import Mapped, mapped_column

from shared.model import NFLTeam, Player
from shared.service.service import Criterion, Filter, Service, filters_model
from shared.service.sql_model import BaseSQLModel
from shared.utils import clean_name
from shared.exceptions import DataIntegrityException
import Levenshtein as levenshtein


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
    injury_status: Mapped[Optional[str]] = mapped_column(default=None)


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
        first_norm = clean_name(first_name)
        res = self.search(
            Criterion.eq("first_name_norm", first_norm)
            & Criterion.eq("last_name_norm", clean_name(last_name))
            & Criterion.eq("team", str(team))
        )
        if len(res.items) == 1:
            return res.items[0]
        if len(res.items) > 1:
            raise DataIntegrityException("get_by_name bad response: " + str(res.items))

        logger.warning(
            "unable to find player from full name - trying last name / team / looser first name"
        )
        # last_name + team is the distinctive part; first_name is where
        # sources disagree (nicknames, "Greg" vs "Gregory"), so drop it
        # first and only bring it back, loosened, if that's ambiguous
        candidates = self.search(
            Criterion.eq("last_name_norm", clean_name(last_name))
            & Criterion.eq("team", str(team))
        ).items
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # more than one same-surname teammate: pick whichever's first
            # name is textually closest to what we were given ("greg" is
            # closer to "gregory" than to any other candidate on the
            # roster) — only decisive if there's a single closest match
            distances = [
                (levenshtein.distance(first_norm, player.first_name_norm), player)
                for player in candidates
            ]
            closest = min(distance for distance, _ in distances)
            narrowed = [player for distance, player in distances if distance == closest]
            if len(narrowed) == 1:
                return narrowed[0]
            candidates = narrowed

        raise DataIntegrityException(
            f"get_by_name bad response for first_name={first_name!r} "
            f"last_name={last_name!r} team={team!r}: {candidates!r}"
        )

    def get_by_full_name(self, full_name: str, team: str | NFLTeam) -> Optional[Player]:
        parts = full_name.strip().split()
        if not parts:
            return None
        first_name = parts[0]
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
        return self.get_by_name(first_name, last_name, team)

    def get_by_number(self, number: int, team: str | NFLTeam) -> Player:
        """Jersey number + team is an exact identifier on its own — unlike
        a name, it needs no normalisation or fallback."""
        res = self.search(
            Criterion.eq("number", number) & Criterion.eq("team", str(team))
        )
        if len(res.items) != 1:
            raise DataIntegrityException(
                f"get_by_number bad response for number={number} team={team!r}: "
                f"{res.items!r}"
            )
        return res.items[0]
