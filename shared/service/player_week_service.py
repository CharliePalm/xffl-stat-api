from typing import Optional

from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from shared.model import PlayerWeekData
from shared.service.service import Service, filters_model
from shared.service.sql_model import BaseSQLModel


class PlayerWeekDataColumns:
    """The `player_week_data` columns, as a declarative mixin.

    Not itself mapped (no `BaseSQLModel` base) — SQLAlchemy copies these
    onto whichever mapped class inherits them. `PlayerWeekDataModel` maps
    them onto the real table; `PlayerStatlineModel` (a read-only view with
    the same stat columns plus a few from `player`) reuses them instead of
    redeclaring every column.
    """

    player_id: Mapped[int] = mapped_column(primary_key=True)
    week: Mapped[int] = mapped_column(primary_key=True)
    points: Mapped[float]

    # offensive stat line
    passing_yards: Mapped[Optional[float]] = mapped_column(default=0.0)
    passing_tds: Mapped[Optional[int]] = mapped_column(default=0)
    interceptions_thrown: Mapped[Optional[int]] = mapped_column(default=0)
    rushing_yards: Mapped[Optional[float]] = mapped_column(default=0.0)
    rushing_tds: Mapped[Optional[int]] = mapped_column(default=0)
    receptions: Mapped[Optional[int]] = mapped_column(default=0)
    receiving_yards: Mapped[Optional[float]] = mapped_column(default=0.0)
    receiving_tds: Mapped[Optional[int]] = mapped_column(default=0)
    fumbles_lost: Mapped[Optional[int]] = mapped_column(default=0)
    two_pt_conversions: Mapped[Optional[int]] = mapped_column(default=0)
    field_goals_made: Mapped[list[int]] = mapped_column(JSON, default=list)
    num_field_goals_missed: Mapped[Optional[int]] = mapped_column(default=0)
    extra_points_points_made: Mapped[Optional[int]] = mapped_column(default=0)

    # defensive stat line
    sacks: Mapped[Optional[int]] = mapped_column(default=0)
    interceptions: Mapped[Optional[int]] = mapped_column(default=0)
    fumbles_recovered: Mapped[Optional[int]] = mapped_column(default=0)
    safeties: Mapped[Optional[int]] = mapped_column(default=0)
    defensive_tds: Mapped[Optional[int]] = mapped_column(default=0)
    blocked_kicks: Mapped[Optional[int]] = mapped_column(default=0)
    points_allowed: Mapped[Optional[int]] = mapped_column(default=0)


class PlayerWeekDataModel(PlayerWeekDataColumns, BaseSQLModel):
    __tablename__ = "player_week_data"


PlayerWeekFilters = filters_model(PlayerWeekData, name="PlayerWeekFilters")


class PlayerWeekService(Service[PlayerWeekDataModel, PlayerWeekData]):
    schema = PlayerWeekData
    row = PlayerWeekDataModel

    # Every stat field filters as plain equality, which is what the
    # inherited `_criterion_for` already does.
