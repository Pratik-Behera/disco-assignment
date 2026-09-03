"""Load catalog JSON shipped with the app."""

from __future__ import annotations

import json
import re
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data"
# Examples are the numbered lines only — skips the `#` header and the `---` rule.
_EXAMPLE = re.compile(r"^\s*\d+\.\s+(\S.*)$")


def load_publishers() -> list[dict]:
    return json.loads((_DATA / "publishers.json").read_text())


def load_personas() -> list[dict]:
    return json.loads((_DATA / "shopper_personas.json").read_text())


def load_examples() -> list[str]:
    lines = (_DATA / "example_advertisers.txt").read_text().splitlines()
    return [m.group(1).strip() for m in map(_EXAMPLE.match, lines) if m]
