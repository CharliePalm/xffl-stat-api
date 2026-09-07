import re

special_chars = ["'", "-", ".", " "]


def to_snake(name: str) -> str:
    """Convert CamelCase / PascalCase to snake_case."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def clean_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name).lower()
