"""Upload-title hygiene (pure, regex-only — no I/O).

Home of `clean_title` (moved out of nightcore.py in Faz 5 P0): deriving a
search query for the ORIGINAL song from a noisy upload title is lrclib-lookup
concern, not timeline math. The nightcore module re-exports it for the
callers that grew up with the old location.
"""

import re

# Title markers that trigger auto-detection when no explicit factor is given.
# \b guards: "Speed Upgrade Tutorial" and "Godspeed Up High" must NOT match
# (retro finding — empirically bitten); [ -]? covers sped-up/sped up/spedup.
NIGHTCORE_TOKENS = re.compile(r"\b(?:nightcore|sped[ -]?up|speed[ -]?up)\b", re.IGNORECASE)

_EMPTY_BRACKETS = re.compile(r"[(\[{]\s*[)\]}]")
_EDGE_SEPARATORS = re.compile(r"^[\s\-–—|:~•/]+|[\s\-–—|:~•/]+$")
_BRACKET_GROUP = re.compile(r"[(\[{]([^)\]}]*)[)\]}]")
_TITLE_WORD = re.compile(r"[\w']+")
# Upload-title noise ("(Lyrics)", "(Official Video)"): removed only when a
# WHOLE bracket group is noise/marker — deleting the words globally mangled
# real titles ("Nightcore - Video Games" → "Games"; retro finding).
_NOISE_WORD = re.compile(
    r"^(?:lyrics?|official|video|audio|visualizer|hq|hd|4k|mv|version)$", re.IGNORECASE
)


def _is_noise_group(content: str) -> bool:
    """A bracket group is droppable when, once the nightcore markers are
    removed, nothing but noise words remains ("Sped-Up Version", "Nightcore",
    "Official Video"). "(Video Games)" keeps its group — "Games" is real."""
    if not _TITLE_WORD.findall(content):
        return False
    remaining = _TITLE_WORD.findall(NIGHTCORE_TOKENS.sub(" ", content))
    return all(_NOISE_WORD.match(w) for w in remaining)


def clean_title(title: str) -> str | None:
    """Search query for the ORIGINAL song, or None when the title carries no
    nightcore/sped-up marker (auto-detection must not run on normal songs)."""
    if not NIGHTCORE_TOKENS.search(title):
        return None
    out = _BRACKET_GROUP.sub(lambda m: " " if _is_noise_group(m.group(1)) else m.group(0), title)
    out = NIGHTCORE_TOKENS.sub(" ", out)
    out = _EMPTY_BRACKETS.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip()
    out = _EDGE_SEPARATORS.sub("", out)
    return out.strip() or None
