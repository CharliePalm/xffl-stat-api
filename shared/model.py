from enum import Enum, StrEnum
from typing import Optional

from pydantic import BaseModel

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
    BUFFALO_BILLS = ("Buffalo Bills", "BUF")
    MIAMI_DOLPHINS = ("Miami Dolphins", "MIA")
    NEW_ENGLAND_PATRIOTS = ("New England Patriots", "NE")
    NEW_YORK_JETS = ("New York Jets", "NYJ")

    # AFC North
    BALTIMORE_RAVENS = ("Baltimore Ravens", "BAL")
    CINCINNATI_BENGALS = ("Cincinnati Bengals", "CIN")
    CLEVELAND_BROWNS = ("Cleveland Browns", "CLE")
    PITTSBURGH_STEELERS = ("Pittsburgh Steelers", "PIT")

    # AFC South
    HOUSTON_TEXANS = ("Houston Texans", "HOU")
    INDIANAPOLIS_COLTS = ("Indianapolis Colts", "IND")
    JACKSONVILLE_JAGUARS = ("Jacksonville Jaguars", "JAX")
    TENNESSEE_TITANS = ("Tennessee Titans", "TEN")

    # AFC West
    DENVER_BRONCOS = ("Denver Broncos", "DEN")
    KANSAS_CITY_CHIEFS = ("Kansas City Chiefs", "KC")
    LAS_VEGAS_RAIDERS = ("Las Vegas Raiders", "LV")
    LOS_ANGELES_CHARGERS = ("Los Angeles Chargers", "LAC")

    # NFC East
    DALLAS_COWBOYS = ("Dallas Cowboys", "DAL")
    NEW_YORK_GIANTS = ("New York Giants", "NYG")
    PHILADELPHIA_EAGLES = ("Philadelphia Eagles", "PHI")
    WASHINGTON_COMMANDERS = ("Washington Commanders", "WAS")

    # NFC North
    CHICAGO_BEARS = ("Chicago Bears", "CHI")
    DETROIT_LIONS = ("Detroit Lions", "DET")
    GREEN_BAY_PACKERS = ("Green Bay Packers", "GB")
    MINNESOTA_VIKINGS = ("Minnesota Vikings", "MIN")

    # NFC South
    ATLANTA_FALCONS = ("Atlanta Falcons", "ATL")
    CAROLINA_PANTHERS = ("Carolina Panthers", "CAR")
    NEW_ORLEANS_SAINTS = ("New Orleans Saints", "NO")
    TAMPA_BAY_BUCCANEERS = ("Tampa Bay Buccaneers", "TB")

    # NFC West
    ARIZONA_CARDINALS = ("Arizona Cardinals", "ARI")
    LOS_ANGELES_RAMS = ("Los Angeles Rams", "LAR")
    SAN_FRANCISCO_49ERS = ("San Francisco 49ers", "SF")
    SEATTLE_SEAHAWKS = ("Seattle Seahawks", "SEA")

    def __init__(self, full_name: str, abbreviation: str):
        self.full_name = full_name
        self.abbreviation = abbreviation

    @classmethod
    def _missing_(cls, value):
        """
        Called automatically by Enum when a value doesn't match any
        member's stored `value` (the (full_name, abbreviation) tuple).
        This lets NFLTeam("CHI") resolve by abbreviation instead of
        requiring the full tuple.
        """
        if isinstance(value, str):
            value_upper = value.upper()
            for team in cls:
                if team.abbreviation == value_upper:
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
    def id(self):
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
    home_team: NFLTeam
    away_team: NFLTeam
    player_week_data: list[PlayerWeekData]


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


# ===== DB MODELS =====


class PlayerWeekData(OffensiveStatLine, DefensiveStatLine):
    player_id: int
    week: int
    points: float


class Player(BaseModel):
    id: int
    first_name: str
    last_name: str
    team: Optional[NFLTeam]
    number: Optional[int]
    active: bool
    position: NFLPosition


class Game(BaseModel):
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
    pos: int
    name: str


# test
if __name__ == "__main__":
    print(NFLTeam("CHI"))
