"""Validate fx_lexicon.yaml — the deterministic gate of the offline lexicon
expansion loop (Faz 6.5 P4). Thin shell: all rules live in
kashi_server.pipeline.lexicon_lint (unit-tested); this maps a file to a
report and an exit code.

The loop it serves (docs/lexicon-expansion-playbook.md):
LLM drafts additions → THIS gate rejects mechanical mistakes (short Turkish
stems, non-normalized İ/I forms, cross-category collisions) → the human
curator judges what's left → the shipped file is re-checked in CI by
tests/test_lexicon_lint.py against the same rules.

Usage:
    uv run python scripts/expand_lexicon.py                 # shipped lexicon
    uv run python scripts/expand_lexicon.py path/to/draft.yaml
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kashi_server.pipeline.lexicon_lint import lint_lexicon  # noqa: E402
from kashi_server.pipeline.semantics import LEXICON_PATH  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else LEXICON_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # unparseable file is the first lint failure
        print(f"ERROR: cannot parse {path}: {exc}")
        return 1
    report = lint_lexicon(raw)
    for message in report.errors:
        print(f"ERROR: {message}")
    for message in report.warnings:
        print(f"warn:  {message}")
    verdict = "CLEAN" if report.ok else "FAILED"
    print(f"{path}: {verdict} ({len(report.errors)} errors, {len(report.warnings)} warnings)")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
