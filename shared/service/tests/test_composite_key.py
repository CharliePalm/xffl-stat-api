"""Regression coverage for `Service.put`/`get`/`exists`/`delete` on a
model with a composite primary key — `PlayerWeekDataModel` (player_id,
week) and `GameModel` (week, home, away). These used to be unusable:
`Service._pk()` raised `ServiceDefinitionError` for any table with more
than one primary key column, since it only ever returned a single
comparable attribute.

`_pk_columns`/`_pk_names`/`_pk_values`/`_pk_predicate` replace it with
support for one or many columns; `id` becomes a scalar for a single-
column key (unchanged) or a tuple in the mapper's own `primary_key` order
for a composite one — the same convention `sqlalchemy.orm.Session.get()`
already uses, which is what lets `_get_row_unscoped` hand `id` straight
to it unchanged either way.
"""

import pytest

from shared.model import Game, PlayerWeekData
from shared.service.engine import build_test_engine
from shared.service.game_service import GameService
from shared.service.player_week_service import PlayerWeekService
from shared.service.sql_model import BaseSQLModel
from sqlalchemy.orm import Session


@pytest.fixture
def session():
    engine = build_test_engine()
    BaseSQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_put_creates_with_explicit_composite_id(session):
    service = PlayerWeekService(session)
    with session.begin():
        result = service.put(None, PlayerWeekData(player_id=1, week=1, points=10.5))

    assert result.created
    assert (result.value.player_id, result.value.week) == (1, 1)
    assert result.value.points == 10.5


def test_put_creates_with_id_inferred_from_payload(session):
    """`id=None` with the payload itself carrying every pk field — same
    as the single-column case, just checked across all of them."""
    service = PlayerWeekService(session)
    with session.begin():
        result = service.put(None, PlayerWeekData(player_id=2, week=3, points=1.0))

    assert result.created
    assert (result.value.player_id, result.value.week) == (2, 3)


def test_put_updates_existing_composite_row(session):
    service = PlayerWeekService(session)
    with session.begin():
        service.put(None, PlayerWeekData(player_id=1, week=1, points=10.5))
    with session.begin():
        result = service.put(
            (1, 1), PlayerWeekData(player_id=1, week=1, points=20.0), partial=True
        )

    assert result.updated
    assert result.value.points == 20.0


def test_get_exists_delete_with_composite_id(session):
    service = PlayerWeekService(session)
    with session.begin():
        service.put(None, PlayerWeekData(player_id=5, week=2, points=7.0))

    assert service.exists((5, 2))
    assert not service.exists((5, 3))

    found = service.get((5, 2))
    assert found is not None
    assert found.points == 7.0
    assert service.get((5, 3)) is None

    assert service.delete((5, 2))
    assert service.get((5, 2)) is None
    assert not service.delete((5, 2))  # already gone


def test_put_rejects_mismatched_composite_id_shape(session):
    """A scalar (or wrong-length tuple) `id` for a composite-key model is
    a caller bug, not an absent row — it should raise, not silently do
    the wrong thing."""
    service = PlayerWeekService(session)
    with session.begin():
        service.put(None, PlayerWeekData(player_id=1, week=1, points=1.0))

    with pytest.raises(ValueError, match="composite primary key"):
        service.get(1)
    with pytest.raises(ValueError, match="composite primary key"):
        service.get((1,))


def test_three_column_composite_key(session):
    """`GameModel`'s key is (week, home, away) — three columns, not two,
    to make sure this isn't accidentally special-cased to pairs."""
    service = GameService(session)
    game = dict(
        week=1,
        home="SF",
        away="LAC",
        cbs_link="",
        espn_link="",
        date_time="2026-01-01 00:00:00",
        pff_id=1,
        neutral_site=False,
        season="2026",
        in_progress=False,
    )
    with session.begin():
        result = service.put(None, game)
    assert result.created
    # `Game.home`/`.away` are typed `NFLTeam`, so `_to_schema` coerces the
    # raw "SF"/"LAC" strings on the way out
    assert (result.value.week, str(result.value.home), str(result.value.away)) == (
        1,
        "SF",
        "LAC",
    )

    assert service.exists((1, "SF", "LAC"))
    found = service.get((1, "SF", "LAC"))
    assert found is not None
    assert not found.in_progress
    session.commit()  # the reads above autobegin a transaction of their own

    with session.begin():
        updated = service.put((1, "SF", "LAC"), {"in_progress": True}, partial=True)
    assert updated.updated
    assert updated.value.in_progress
    session.commit()

    assert service.delete((1, "SF", "LAC"))
    assert service.get((1, "SF", "LAC")) is None
