from enum import Enum, StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from enum import Enum

TEAM_IDS = {
    "ARI": -1,
    "ATL": -2,
    "BAL": -3,
    "BUF": -4,
    "CAR": -5,
    "CHI": -6,
    "CIN": -7,
    "CLE": -8,
    "DAL": -9,
    "DEN": -10,
    "DET": -11,
    "GB": -12,
    "HOU": -13,
    "IND": -14,
    "JAX": -15,
    "KC": -16,
    "LAC": -17,
    "LAR": -18,
    "LV": -19,
    "MIA": -20,
    "MIN": -21,
    "NE": -22,
    "NO": -23,
    "NYG": -24,
    "NYJ": -25,
    "PHI": -26,
    "PIT": -27,
    "SEA": -28,
    "SF": -29,
    "TB": -30,
    "TEN": -31,
    "WAS": -32,
}


class NFLTeam(Enum):
    # AFC East
    BUFFALO_BILLS = ("Buffalo Bills", "BUF", "4")
    MIAMI_DOLPHINS = ("Miami Dolphins", "MIA", "20")
    NEW_ENGLAND_PATRIOTS = ("New England Patriots", "NE", "22")
    NEW_YORK_JETS = ("New York Jets", "NYJ", "25")

    # AFC North
    BALTIMORE_RAVENS = ("Baltimore Ravens", "BAL", "3")
    CINCINNATI_BENGALS = ("Cincinnati Bengals", "CIN", "7")
    CLEVELAND_BROWNS = ("Cleveland Browns", "CLE", "8")
    PITTSBURGH_STEELERS = ("Pittsburgh Steelers", "PIT", "26")

    # AFC South
    HOUSTON_TEXANS = ("Houston Texans", "HOU", "13")
    INDIANAPOLIS_COLTS = ("Indianapolis Colts", "IND", "14")
    JACKSONVILLE_JAGUARS = ("Jacksonville Jaguars", "JAX", "15")
    TENNESSEE_TITANS = ("Tennessee Titans", "TEN", "31")

    # AFC West
    DENVER_BRONCOS = ("Denver Broncos", "DEN", "10")
    KANSAS_CITY_CHIEFS = ("Kansas City Chiefs", "KC", "16")
    LAS_VEGAS_RAIDERS = ("Las Vegas Raiders", "LV", "17")
    LOS_ANGELES_CHARGERS = ("Los Angeles Chargers", "LAC", "18")

    # NFC East
    DALLAS_COWBOYS = ("Dallas Cowboys", "DAL", "9")
    NEW_YORK_GIANTS = ("New York Giants", "NYG", "24")
    PHILADELPHIA_EAGLES = ("Philadelphia Eagles", "PHI", "27")
    WASHINGTON_COMMANDERS = ("Washington Commanders", "WAS", "32")

    # NFC North
    CHICAGO_BEARS = ("Chicago Bears", "CHI", "6")
    DETROIT_LIONS = ("Detroit Lions", "DET", "11")
    GREEN_BAY_PACKERS = ("Green Bay Packers", "GB", "12")
    MINNESOTA_VIKINGS = ("Minnesota Vikings", "MIN", "21")

    # NFC South
    ATLANTA_FALCONS = ("Atlanta Falcons", "ATL", "2")
    CAROLINA_PANTHERS = ("Carolina Panthers", "CAR", "5")
    NEW_ORLEANS_SAINTS = ("New Orleans Saints", "NO", "23")
    TAMPA_BAY_BUCCANEERS = ("Tampa Bay Buccaneers", "TB", "30")

    # NFC West
    ARIZONA_CARDINALS = ("Arizona Cardinals", "ARI", "1")
    LOS_ANGELES_RAMS = ("Los Angeles Rams", "LAR", "19")
    SAN_FRANCISCO_49ERS = ("San Francisco 49ers", "SF", "28")
    SEATTLE_SEAHAWKS = ("Seattle Seahawks", "SEA", "29")

    def __init__(self, full_name: str, abbreviation: str, team_id: str):
        self.full_name = full_name
        self.abbreviation = abbreviation
        self.team_id = team_id

    @classmethod
    def _missing_(cls, value):  # type: ignore
        """
        Called automatically by Enum when a value doesn't match any
        member's stored `value` (the (full_name, abbreviation, team_id) tuple).
        This lets NFLTeam("CHI"), NFLTeam("Chicago Bears"), and NFLTeam("6")
        all resolve by abbreviation, full name, or team id instead of
        requiring the full tuple.
        """
        value_str = str(value).strip().upper()
        for team in cls:
            if value_str in (
                team.abbreviation.upper(),
                team.full_name.upper(),
                str(team.team_id).upper(),
            ):
                return team
        return None  # triggers the normal ValueError Enum raises on lookup failure

    @property
    def team_name(self):
        return self.full_name.split(" ")[-1]

    @property
    def team_city(self):
        return str.join(" ", self.full_name.split(" ")[0:-1])

    @classmethod
    def from_abbreviation(cls, abbr: str):
        """Look up team Enum member by abbreviation string."""
        for team in cls:
            if team.abbreviation == abbr.upper():
                return team
        raise ValueError(f"Unknown NFL team abbreviation: {abbr}")

    def __str__(self):
        return self.abbreviation

    @property
    def player_defense_id(self):
        return TEAM_IDS[self.abbreviation]


class NFLPosition(StrEnum):
    # Core Roster Positions
    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    D = "D"


class ScrapedPageInfo(BaseModel):
    game: Game
    player_week_data: list[PlayerWeekData]


# ===== DB MODELS =====


class DefensiveStatLine(BaseModel):
    sacks: Optional[int] = 0
    interceptions: Optional[int] = 0
    fumbles_recovered: Optional[int] = 0
    safeties: Optional[int] = 0
    defensive_tds: Optional[int] = 0
    blocked_kicks: Optional[int] = 0
    points_allowed: Optional[int] = 0


class OffensiveStatLine(BaseModel):
    passing_yards: Optional[float] = 0.0
    passing_tds: Optional[int] = 0
    interceptions_thrown: Optional[int] = 0
    rushing_yards: Optional[float] = 0.0
    rushing_tds: Optional[int] = 0
    receptions: Optional[int] = 0
    receiving_yards: Optional[float] = 0.0
    receiving_tds: Optional[int] = 0
    fumbles_lost: Optional[int] = 0
    two_pt_conversions: Optional[int] = 0
    field_goals_made: list[int] = []  # list of field goal distances made
    num_field_goals_missed: Optional[int] = 0
    extra_points_points_made: Optional[int] = 0


class PlayerWeekData(OffensiveStatLine, DefensiveStatLine):
    model_config = ConfigDict(from_attributes=True)

    _id_keys = ["player_id", "week"]
    player_id: int
    week: int
    points: float


class Player(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    team: Optional[NFLTeam] = Field(None)
    number: Optional[int] = None
    active: bool
    position: NFLPosition
    first_name_norm: str
    last_name_norm: str
    tank_id: Optional[str] = None
    espn_id: Optional[str] = None
    yahoo_id: Optional[str] = None
    cbs_id: Optional[str] = None
    fantasy_pros_id: Optional[str] = None
    f_ref_id: Optional[str] = None
    roto_wire_id: Optional[str] = None
    injury_status: Optional[str] = None


class Game(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    week: int
    home: NFLTeam
    away: NFLTeam
    cbs_link: str
    espn_link: str
    date_time: str
    pff_id: int
    neutral_site: bool
    season: str
    in_progress: bool


class Provider(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pos: int
    name: str
    uses: int
    last_used: str = ""


class PlayerStatline(PlayerWeekData):
    first_name: str
    last_name: str
    team: NFLTeam
    position: NFLPosition
