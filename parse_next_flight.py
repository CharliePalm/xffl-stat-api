#!/usr/bin/env python3
"""
Extract JSON payloads out of Next.js flight-stream push calls, e.g.:

    self.__next_f.push([1,"87:{\"some\":\"json\"}"])
    self.__next_f.push([1,"88:[1,2,3]"])

and write them pretty-printed to a file.

Usage:
    python parse_next_flight.py input.html -o output.json
    python parse_next_flight.py input.html            # writes input.parsed.json
    cat input.html | python parse_next_flight.py -     # read from stdin
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PUSH_CALL_START = "self.__next_f.push("


def _find_matching_paren(text: str, open_paren_index: int) -> int:
    """Given the index of an opening '(', return the index of its matching
    ')', respecting string literals (so parens/brackets inside quoted
    strings don't throw off the depth count)."""
    depth = 0
    i = open_paren_index
    in_string = False
    string_quote = ""
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2  # skip escaped char
                continue
            if ch == string_quote:
                in_string = False
        else:
            if ch in ("'", '"'):
                in_string = True
                string_quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    raise ValueError("Unbalanced parentheses - reached end of text without a match")


def extract_push_call_args(text: str) -> list[str]:
    """Find every `self.__next_f.push(...)` call and return the raw text
    inside the parentheses for each one."""
    args = []
    start = 0
    while True:
        idx = text.find(PUSH_CALL_START, start)
        if idx == -1:
            break
        open_paren = idx + len(PUSH_CALL_START) - 1
        close_paren = _find_matching_paren(text, open_paren)
        args.append(text[open_paren + 1 : close_paren])
        start = close_paren + 1
    return args


def parse_payload(raw_payload: str) -> tuple[str | None, object]:
    """Given the string payload from a push call (the second element of the
    pushed array, e.g. '87:{"some":"json"}'), split off the leading
    '<id>:' prefix and JSON-decode the remainder.

    Returns (prefix, parsed_value). If the remainder isn't valid JSON
    (flight streams sometimes prefix a type letter, e.g. '1:I[...]', or
    contain plain strings), parsed_value falls back to the raw remainder
    string so nothing is silently dropped.
    """
    match = re.match(r"^(\d+):(.*)$", raw_payload, re.DOTALL)
    if not match:
        return None, raw_payload
    prefix, remainder = match.group(1), match.group(2)
    try:
        return prefix, json.loads(remainder)
    except json.JSONDecodeError:
        # Some chunks have a one-letter type tag before the JSON, e.g. "I[...]"
        tag_match = re.match(r"^([A-Za-z]+)(.*)$", remainder, re.DOTALL)
        if tag_match:
            try:
                return prefix, json.loads(tag_match.group(2))
            except json.JSONDecodeError:
                pass
        return prefix, remainder


def extract_all(text: str) -> list[dict]:
    results = []
    for raw_args in extract_push_call_args(text):
        # raw_args looks like: [1,"87:{...}"]  -- already a full JSON array literal
        try:
            outer = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            print(
                f"warning: skipping a push() call, couldn't parse its arguments: {exc}",
                file=sys.stderr,
            )
            continue
        if len(outer) < 2 or not isinstance(outer[1], str):
            continue
        chunk_id, string_payload = outer[0], outer[1]
        prefix, parsed = parse_payload(string_payload)
        results.append({"chunk_id": chunk_id, "prefix": prefix, "data": parsed})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "input",
        help="Input file containing the self.__next_f.push(...) calls, or '-' for stdin",
    )
    parser.add_argument(
        "-o", "--output", help="Output file path (default: <input>.parsed.json)"
    )
    args = parser.parse_args()

    if args.input == "-":
        text = sys.stdin.read()
        default_output = Path("output.parsed.json")
    else:
        input_path = Path(args.input)
        text = input_path.read_text(encoding="utf-8")
        default_output = input_path.with_suffix(".parsed.json")

    results = extract_all(text)
    if not results:
        print(
            "No self.__next_f.push(...) calls found (or none could be parsed).",
            file=sys.stderr,
        )
        sys.exit(1)

    # If there's only one chunk, write its data directly rather than
    # wrapping it in a single-element list, since that's usually more
    # useful to look at.
    output_data = results[0]["data"] if len(results) == 1 else results

    output_path = Path(args.output) if args.output else default_output
    output_path.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {len(results)} chunk(s) to {output_path}")


if __name__ == "__main__":
    main()
