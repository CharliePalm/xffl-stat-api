"""Helpers for reading data out of a Next.js RSC ("flight") payload.

Sleeper's page ships no rendered box-score DOM — the data streams down as a
series of `self.__next_f.push([id, "..."])` script calls, where each string is
an escaped fragment of one long text stream containing embedded JSON objects.
This reconstructs that stream and finds JSON objects within it, so a scraper
can work with plain dicts instead of walking HTML.
"""

import json
import re
from typing import Any

_PUSH_CALL = re.compile(r'self\.__next_f\.push\(\[\d+,"((?:[^"\\]|\\.)*)"\]\)')


def reconstruct_flight_text(html: str) -> str:
    """Concatenate every push call's unescaped string, in document order."""
    return "".join(json.loads('"' + chunk + '"') for chunk in _PUSH_CALL.findall(html))


def find_enclosing_object(text: str, pos: int) -> tuple[int, int] | None:
    """Smallest balanced `{...}` span in `text` that starts at/before `pos` and contains it."""
    start = text.rfind("{", 0, pos)
    while start >= 0:
        depth = 0
        in_str = False
        escaped = False
        i = start
        while i < len(text):
            char = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_str = False
            elif char == '"':
                in_str = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    if i >= pos:
                        return start, i + 1
                    break
            i += 1
        start = text.rfind("{", 0, start)
    return None


def find_objects(
    text: str, marker: str, required_keys: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Parse the enclosing object around every occurrence of `marker` in `text`.

    `required_keys` filters out objects that happen to contain the marker text
    without being the record we're after (e.g. `marker` also appearing nested
    inside a larger sibling object).
    """
    objects: list[dict[str, Any]] = []
    for match in re.finditer(re.escape(marker), text):
        span = find_enclosing_object(text, match.start())
        if span is None:
            continue
        try:
            obj = json.loads(text[span[0] : span[1]])
        except json.JSONDecodeError:
            continue
        if all(key in obj for key in required_keys):
            objects.append(obj)
    return objects
