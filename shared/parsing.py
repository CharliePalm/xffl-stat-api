"""Parsing helpers shared by every scraper, independent of any one site's markup."""

import re

from shared.model import NFLPosition, NFLTeam

# both ESPN and CBS link players as ".../<id>/<first>-<last>", which gives a
# canonical full name even where the table itself abbreviates ("D. Lock")
PLAYER_SLUG = re.compile(r"/([a-z0-9'.-]+-[a-z0-9'.-]+)/?$", re.IGNORECASE)
# name suffixes that plain capitalization would mangle
NAME_SUFFIXES = {
    "jr": "Jr.",
    "sr": "Sr.",
    "ii": "II",
    "iii": "III",
    "iv": "IV",
    "v": "V",
}


def to_float(value: str | None) -> float:
    """Stat cells are absent ('--', ''), plain ('126'), or compound ('11/15', '1-5')."""
    if not value:
        return 0.0
    try:
        return float(value.strip().rstrip("%"))
    except ValueError:
        return 0.0


def leading_int(value: str | None, separators: str = "/-") -> int:
    """First number of a compound cell — '11/15' -> 11, '2-17' -> 2."""
    if not value:
        return 0
    head = re.split(f"[{re.escape(separators)}]", value.strip(), maxsplit=1)[0]
    return int(to_float(head))


def name_from_url(url: str | None, fallback: str) -> str:
    """Prefer the profile-link slug so the same player matches across sources."""
    match = PLAYER_SLUG.search(url or "")
    if not match:
        return fallback
    parts = match.group(1).split("-")
    return " ".join(
        NAME_SUFFIXES.get(part.lower(), part.capitalize()) for part in parts
    )


def team_from_nickname(nickname: str) -> NFLTeam | None:
    """Resolve a bare nickname ('Browns', 'Seahawks') — all 32 are unique."""
    nickname = nickname.strip()
    if not nickname:
        return None
    for team in NFLTeam:
        if team.full_name.endswith(nickname):
            return team
    return None


def position_from_text(text: str | None) -> NFLPosition | None:
    """Pull a fantasy position out of e.g. '2 QB'; non-fantasy positions give None."""
    for token in re.findall(r"[A-Z/]{1,4}", (text or "").upper()):
        try:
            return NFLPosition(token)
        except ValueError:
            continue
    return None
