from kashi_server.pipeline.langid import DEFAULT_LANGUAGE, detect_language


def test_english_and_turkish():
    assert detect_language("we're no strangers to love, you know the rules") == "eng"
    assert detect_language("bir sevgi masalı bu, kalbimin en derininde") == "tur"


def test_newlines_do_not_break_detection():
    assert detect_language("hello there\nhow are you doing today") == "eng"


def test_empty_and_unknown_fall_back_to_english():
    assert detect_language("") == DEFAULT_LANGUAGE
    assert detect_language("   ") == DEFAULT_LANGUAGE


def test_detection_failure_never_raises(monkeypatch):
    import fast_langdetect

    def boom(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(fast_langdetect, "detect", boom)
    assert detect_language("anything at all") == DEFAULT_LANGUAGE
