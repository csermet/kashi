"""benchmarks.datasets loaders against a synthetic mini-checkout (no download)."""

import pytest

from benchmarks.datasets import KashiCase, ensure_jamendo, load_cases, load_jamendo

CSV_HEADER = (
    "URL,Filepath,Artist,Title,Genre,LicenseType,Language,LyricOverlap,Polyphonic,NonLexical"
)


def _mini_checkout(tmp_path):
    root = tmp_path / "jamendolyrics"
    (root / "lyrics").mkdir(parents=True)
    (root / "annotations" / "words").mkdir(parents=True)
    (root / "JamendoLyrics.csv").write_text(
        f"{CSV_HEADER}\n"
        "u,Artist_-_Song.mp3,Artist,Song,Pop,BY-ND,English,false,false,false\n"
        "u,Otro_-_Cancion.mp3,Otro,Cancion,Pop,CC BY-NC-SA,Spanish,false,false,false\n"
    )
    # 4 words over 2 lines; line_end nan for internal words, a timestamp on finals.
    (root / "lyrics" / "Artist_-_Song.words.txt").write_text("hello world bye now")
    (root / "annotations" / "words" / "Artist_-_Song.csv").write_text(
        "word_start,word_end,line_end\n"
        "1.0,1.5,nan\n"
        "1.5,2.0,2.0\n"
        "10.0,10.5,nan\n"
        "10.5,11.0,11.0\n"
    )
    (root / "lyrics" / "Otro_-_Cancion.words.txt").write_text("hola mundo")
    (root / "annotations" / "words" / "Otro_-_Cancion.csv").write_text(
        "word_start,word_end,line_end\n0.5,1.0,nan\n1.0,1.4,1.4\n"
    )
    return root


def test_load_jamendo_rebuilds_lines_from_word_annotations(tmp_path):
    songs = load_jamendo(_mini_checkout(tmp_path))
    assert [s.stem for s in songs] == ["Artist_-_Song", "Otro_-_Cancion"]
    song = songs[0]
    assert song.language == "eng"
    assert song.line_texts == ["hello world", "bye now"]
    assert song.line_starts_ms == [1000, 10000]
    assert song.words == [(1000, "hello"), (1500, "world"), (10000, "bye"), (10500, "now")]
    assert song.duration_hint_s == 11.0
    assert song.audio_path.name == "Artist_-_Song.mp3"
    # aligner input tokenizes back to exactly the annotated word sequence
    assert " ".join(song.line_texts).split() == [w for _, w in song.words]


def test_load_jamendo_filters(tmp_path):
    root = _mini_checkout(tmp_path)
    assert [s.language for s in load_jamendo(root, languages={"spa"})] == ["spa"]
    assert len(load_jamendo(root, limit=1)) == 1
    assert [s.stem for s in load_jamendo(root, stems={"Otro_-_Cancion"})] == ["Otro_-_Cancion"]


def test_turkish_loads_through_the_same_reader(tmp_path):
    """The whole reason the hand-built TR set is written in this schema: no
    loader fork, so every metric, flag and threshold applies to it unchanged.
    JamendoLyrics itself has no Turkish — this row can only come from ours."""
    root = _mini_checkout(tmp_path)
    (root / "JamendoLyrics.csv").write_text(
        f"{CSV_HEADER}\n"
        "u,Tarkan_-_Sarki.mp3,Tarkan,Sarki,Pop,internal-eval-only,Turkish,false,false,false\n"
    )
    (root / "lyrics" / "Tarkan_-_Sarki.words.txt").write_text("şımarık kuzu")
    (root / "annotations" / "words" / "Tarkan_-_Sarki.csv").write_text(
        "word_start,word_end,line_end\n0.5,1.0,nan\n1.0,1.4,1.4\n"
    )
    songs = load_jamendo(root, languages={"tur"})
    assert [s.language for s in songs] == ["tur"]
    assert songs[0].words == [(500, "şımarık"), (1000, "kuzu")]


def test_verified_column_marks_which_words_a_human_checked(tmp_path):
    """Hand-built sets annotate a SAMPLE, because measuring the first song
    showed 96% of the correction effort moved words by less than the 300 ms
    PCO tolerance. The unchecked rows keep their pre-mark so the row count
    still matches the tokens; `verified` says which ones may be scored."""
    root = _mini_checkout(tmp_path)
    (root / "annotations" / "words" / "Artist_-_Song.csv").write_text(
        "word_start,word_end,line_end,verified\n"
        "1.0,1.5,nan,1\n"
        "1.5,2.0,2.0,0\n"
        "10.0,10.5,nan,0\n"
        "10.5,11.0,11.0,1\n"
    )
    song = load_jamendo(root, stems={"Artist_-_Song"})[0]
    assert song.verified == [True, False, False, True]
    assert len(song.words) == 4  # every row still loads; only scoring differs


def test_a_set_without_the_column_scores_every_word(tmp_path):
    # JamendoLyrics itself has no `verified` column, and every word in it IS
    # ground truth — absence must mean "all", never "none".
    song = load_jamendo(_mini_checkout(tmp_path), stems={"Artist_-_Song"})[0]
    assert song.verified is None


def test_a_local_root_is_never_silently_downloaded(tmp_path):
    """A missing TR root must fail loudly. The default path fetches 390 MB on
    a miss, and inheriting that for a set that is assembled by hand would turn
    a typo into a surprise download of the WRONG dataset."""
    with pytest.raises(SystemExit, match="assembled, not"):
        ensure_jamendo(tmp_path, "jamendolyrics-tr")


def test_load_jamendo_token_mismatch_raises(tmp_path):
    root = _mini_checkout(tmp_path)
    (root / "lyrics" / "Artist_-_Song.words.txt").write_text("hello world bye")  # 3 vs 4
    with pytest.raises(ValueError, match="word rows vs"):
        load_jamendo(root, stems={"Artist_-_Song"})


def test_load_cases_parses_window_and_defaults(tmp_path):
    path = tmp_path / "cases.yaml"
    path.write_text(
        "- id: a\n  title: T\n  artist: A\n  youtube_id: y1\n  lrclib_id: 7\n"
        "  window_s: [29, 65]\n"
        "- id: b\n  title: U\n  artist: B\n  youtube_id: y2\n  lrclib_id: 8\n"
        "  language: spa\n"
    )
    cases = load_cases(path)
    assert cases[0] == KashiCase(
        id="a", title="T", artist="A", youtube_id="y1", lrclib_id=7,
        language="eng", window_s=(29.0, 65.0),
    )
    assert cases[1].language == "spa" and cases[1].window_s is None
