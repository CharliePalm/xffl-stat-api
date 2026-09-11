from datetime import datetime
import re
import unicodedata
from zoneinfo import ZoneInfo

special_chars = ["'", "-", ".", " "]

# trailing generational suffix. "jr"/"sr" require the period — those two
# letters alone are common enough inside real names (e.g. "Jrue") that
# stripping them unconditionally risks mangling one; a roman numeral
# ("ii", "iii", ...) isn't a real name fragment either way, so it's
# stripped with or without a trailing period.
_GENERATIONAL_SUFFIX = re.compile(r" (?:jr\.|sr\.|ii|iii|iv|v|vi)\.?$", re.IGNORECASE)


def to_snake(name: str) -> str:
    """Convert CamelCase / PascalCase to snake_case."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def clean_name(name: str) -> str:
    """Fold to a bare lowercase alnum key so the same person matches
    across sources regardless of case, punctuation, accents, or a
    generational suffix — "Eddy Piñeiro" / "Eddy Pineiro" and
    "Brian Robinson Jr." / "Brian Robinson" all collapse to the same key.

    NFKD decomposition splits an accented letter into its base letter
    plus a combining mark (e.g. "ñ" -> "n" + "◌̃"); stripping unicode
    category "Mn" (combining marks) then drops just the accent.
    """
    without_suffix = _GENERATIONAL_SUFFIX.sub("", name)
    decomposed = unicodedata.normalize("NFKD", without_suffix)
    without_accents = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"[^A-Za-z0-9]", "", without_accents).lower()


def now() -> datetime:
    return datetime.now(ZoneInfo("America/Chicago"))


def date_format(date: datetime) -> str:
    return date.strftime("%Y-%m-%d %H:%M:%S")


def now_str() -> str:
    return date_format(now())
