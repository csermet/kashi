"""fx-lexicon lint (Faz 6.5 P4) — the deterministic half of the offline
expansion tool.

The LLM drafts lexicon additions; THIS module is the gate they must pass
before a human even reads them: schema shape, id charset (the overlay's
FX_TAG_RE twin), Turkish stem discipline (>=4 chars, written pre-normalized
— the İ/I trap surfaces here, not in production), duplicate and
cross-category collision detection. Pure functions over the parsed YAML so
every rule is unit-tested; the CLI shell lives in scripts/expand_lexicon.py.

Severity model: ERROR fails the gate (exit 1 / test failure), WARN is a
human-judgement flag (reported, never fatal) — e.g. a keyword another
category's stem would also catch.
"""

import re
from dataclasses import dataclass, field

from kashi_server.pipeline.semantics import MIN_STEM_LEN, normalize

# The overlay gates fx tags with the same charset before any DOM use — an id
# that fails here would render effect-less client-side (mapFx drops it).
TAG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
VERSION_RE = re.compile(r"^kashi-fx/\d+\.\d+\.\d+$")
_TOKEN_RE = re.compile(r"^[^\W\d_]+$", re.UNICODE)

_LIST_FIELDS = (
    "keywords_en",
    "keywords_tr",
    "stems_en",
    "stems_tr",
    "variants_tr",
    "prototypes_en",
    "prototypes_tr",
)


@dataclass
class LintReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _str_list(cat: dict, key: str) -> list[str]:
    value = cat.get(key) or []
    return [v for v in value if isinstance(v, str)]


def lint_lexicon(raw: object) -> LintReport:
    """Validate a parsed fx_lexicon.yaml document. Deterministic; no I/O."""
    report = LintReport()
    if not isinstance(raw, dict):
        report.error("top level must be a mapping")
        return report

    version = raw.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        report.error(f"version must match kashi-fx/X.Y.Z (got {version!r})")

    categories = raw.get("categories")
    if not isinstance(categories, list) or not categories:
        report.error("categories must be a non-empty list")
        return report

    seen_ids: set[str] = set()
    # (normalized entry, kind, category) for cross-category collision checks.
    keyword_owner: dict[str, str] = {}
    stem_owner: dict[str, str] = {}

    for index, cat in enumerate(categories):
        if not isinstance(cat, dict):
            report.error(f"categories[{index}] must be a mapping")
            continue
        cid = cat.get("id")
        label = cid if isinstance(cid, str) else f"categories[{index}]"
        if not isinstance(cid, str) or not TAG_ID_RE.match(cid):
            report.error(f"{label}: id must match {TAG_ID_RE.pattern}")
        elif cid in seen_ids:
            report.error(f"{label}: duplicate category id")
        else:
            seen_ids.add(cid)

        icon = cat.get("icon")
        if not isinstance(icon, str) or not icon.strip():
            report.error(f"{label}: icon must be a non-empty string")

        intensity = cat.get("base_intensity")
        if not isinstance(intensity, (int, float)) or not 0 <= float(intensity) <= 1:
            report.error(f"{label}: base_intensity must be a number in [0, 1]")

        for key in _LIST_FIELDS:
            value = cat.get(key)
            if value is not None and (
                not isinstance(value, list) or any(not isinstance(v, str) for v in value)
            ):
                report.error(f"{label}: {key} must be a list of strings")

        unknown = set(cat) - {"id", "icon", "base_intensity", *_LIST_FIELDS}
        if unknown:
            report.error(f"{label}: unknown fields {sorted(unknown)}")

        # --- entry discipline ------------------------------------------------
        keywords = _str_list(cat, "keywords_en") + _str_list(cat, "keywords_tr")
        variants = _str_list(cat, "variants_tr")
        stems = _str_list(cat, "stems_en") + _str_list(cat, "stems_tr")

        for entry in keywords + variants + stems:
            if entry != normalize(entry):
                # Written non-normalized (uppercase İ/I etc.): production
                # would still match, but the file must show the exact string
                # the matcher sees — this is where the İ/I trap gets caught.
                report.error(
                    f"{label}: entry {entry!r} is not normalized "
                    f"(write it as {normalize(entry)!r})"
                )
            if not _TOKEN_RE.match(normalize(entry)):
                report.error(
                    f"{label}: entry {entry!r} is not a single letter-only token"
                )

        for stem in stems:
            if len(normalize(stem)) < MIN_STEM_LEN:
                report.error(
                    f"{label}: stem {stem!r} is shorter than {MIN_STEM_LEN} chars "
                    f"(the matcher would ignore it silently)"
                )

        # Duplicates inside the category (keywords + variants share the
        # exact-match namespace in load_lexicon).
        exact = [normalize(e) for e in keywords + variants]
        for dup in sorted({e for e in exact if exact.count(e) > 1}):
            report.warn(f"{label}: duplicate exact entry {dup!r} within the category")
        norm_stems = [normalize(s) for s in stems]
        for dup in sorted({s for s in norm_stems if norm_stems.count(s) > 1}):
            report.warn(f"{label}: duplicate stem {dup!r} within the category")
        for entry in sorted(set(exact)):
            covering = [s for s in norm_stems if entry.startswith(s)]
            if covering:
                report.warn(
                    f"{label}: keyword {entry!r} is already covered by own stem "
                    f"{covering[0]!r} (redundant, harmless)"
                )

        # --- cross-category collisions --------------------------------------
        if isinstance(cid, str):
            for entry in sorted(set(exact)):
                owner = keyword_owner.setdefault(entry, cid)
                if owner != cid:
                    report.error(
                        f"keyword {entry!r} claimed by both '{owner}' and '{cid}' "
                        f"— one word, one category"
                    )
            for stem in sorted(set(norm_stems)):
                owner = stem_owner.setdefault(stem, cid)
                if owner != cid:
                    report.error(
                        f"stem {stem!r} claimed by both '{owner}' and '{cid}'"
                    )

        # --- prototypes (embedding centroids need real sentences) -----------
        for key in ("prototypes_en", "prototypes_tr"):
            protos = _str_list(cat, key)
            if not protos:
                report.error(f"{label}: {key} must have at least one sentence")
            for proto in protos:
                if len(proto.split()) < 3:
                    report.warn(
                        f"{label}: {key} entry {proto!r} looks like a bare word — "
                        f"prototypes should be short definition sentences (R2)"
                    )

    # Cross-category prefix shadowing: cat A's stem being a PREFIX of cat B's
    # exact keyword means A steals B's word at match time (stem check runs on
    # the same token). Deterministic outcome, but a human must intend it.
    for entry, owner in sorted(keyword_owner.items()):
        for stem, stem_cat in sorted(stem_owner.items()):
            if stem_cat != owner and entry.startswith(stem):
                report.warn(
                    f"keyword {entry!r} ('{owner}') also matches stem {stem!r} "
                    f"('{stem_cat}') — higher base_intensity wins; confirm intent"
                )
    return report
