"""Language detection for the aligner (which wants ISO-639-3 codes)."""

import logging

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "eng"

# Only the languages we realistically meet, mapped to the ISO-639-3 codes the
# aligner wants. A language OUTSIDE this map passes through as its raw code
# (see detect_language) rather than becoming English. That mattered less when
# every language hit the same multilingual model — a wrong hint cost accuracy,
# not correctness — but since Faz 8.1 the language ROUTES: an unknown language
# labelled "eng" would be aligned by the English-vocabulary checkpoint AND
# take the English lateness corrections, which is a regression from the MMS
# fallback it should get. The aligner side is safe with a raw code: uroman
# ignores unknown lcodes and text_normalize falls back to its "*" config
# (verified in the shipped ctc-forced-aligner, 2026-08-12).
_ISO_639_1_TO_3 = {
    "en": "eng",
    "tr": "tur",
    "de": "deu",
    "fr": "fra",
    "es": "spa",
    "it": "ita",
    "pt": "por",
    "ru": "rus",
    "ja": "jpn",
    "ko": "kor",
}


def to_iso639_3(code: str) -> str:
    """Normalize a language code to the form the aligner is called with.

    "en" -> "eng", "ENG" -> "eng". An unknown code passes through lowercased
    rather than becoming English: this is used to look up configuration, where
    a wrong key must miss (and fall back visibly) instead of matching English.
    """
    normalized = code.strip().lower()
    return _ISO_639_1_TO_3.get(normalized, normalized)


def detect_language(text: str) -> str:
    if not text.strip():
        return DEFAULT_LANGUAGE
    try:
        from fast_langdetect import detect

        # model="lite": the ~1 MB model ships with the package. The default
        # ("auto") downloads a 125 MB one at first call — an unwelcome surprise
        # inside a worker container, and overkill for picking one of ten codes.
        # The fasttext backend also chokes on embedded newlines.
        results = detect(text.replace("\n", " ").strip(), model="lite", k=1)
        code = str(results[0]["lang"]).lower() if results else ""
    except Exception as exc:  # detection is a nicety, never a job failure
        logger.warning("language detection failed (%s); assuming %s", exc, DEFAULT_LANGUAGE)
        return DEFAULT_LANGUAGE
    if not code:
        return DEFAULT_LANGUAGE
    # Detected-but-unmapped (zh, ar, pl, ...): pass the raw code through so the
    # routing table MISSES and the song gets the multilingual fallback with no
    # language-specific corrections. "eng" stays the answer only when we have
    # no detection at all — a guess, and English is this archive's best guess.
    return _ISO_639_1_TO_3.get(code, code)
