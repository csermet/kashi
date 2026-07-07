"""Validates the shared schema fixtures from the Python side.

Mirrors packages/schemas/scripts/validate-examples.mjs so both language
ecosystems prove they can consume the same contract. Fixture tests are strict;
production parsers stay tolerant (extra='allow') per the additive-only rule.
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMAS_DIR = Path(__file__).parents[3] / "packages" / "schemas"
SCHEMA = json.loads((SCHEMAS_DIR / "processed-track.v1.schema.json").read_text())
EXAMPLES = sorted((SCHEMAS_DIR / "examples").glob("*.json"))


@pytest.mark.parametrize("example_path", EXAMPLES, ids=lambda p: p.name)
def test_example_validates(example_path: Path) -> None:
    doc = json.loads(example_path.read_text())
    Draft202012Validator(SCHEMA).validate(doc)

    for i, line in enumerate(doc["lines"]):
        if doc["sync"] == "word":
            assert line.get("words"), f"lines[{i}]: sync=word requires non-empty words"
        if doc["sync"] == "line":
            assert "words" not in line, f"lines[{i}]: sync=line forbids words"


def test_examples_exist() -> None:
    names = {p.name for p in EXAMPLES}
    assert {"word.json", "line-only.json"} <= names
