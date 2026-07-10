"""Language detection for the aligner (which wants ISO-639-3 codes)."""

import logging

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "eng"

# Only the languages we realistically meet. Anything else falls back to English:
# the MMS model romanizes its input anyway, so a wrong hint costs accuracy, not
# correctness.
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
    return _ISO_639_1_TO_3.get(code, DEFAULT_LANGUAGE)
