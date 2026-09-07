from enum import StrEnum
import uuid
from datetime import UTC, datetime

from pydantic import EmailStr
from sqlalchemy import DateTime
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


class Team(StrEnum):
    a = "a"


class Game(SQLModel):
    week: int
    home: Team  # todo
    away: Team
    cbsLink: str
    espnLink: str
    in_progress: bool = Field(False)
    season: str

class Player(SQLModel):
