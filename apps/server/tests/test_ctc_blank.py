"""The CTC blank of every greedy reader of the emissions is the aligner's own.

`transcribe` (the by-ear rung), `heard_tokens` (the sheet shift's ear) and the
audit's ear used to look the blank up as `vocab["<pad>"]`. Right for the three
production checkpoints, wrong for a checkpoint whose pad token is not named
`<pad>`: omniASR-CTC's tokenizer has pad `<s>` = 0 and a `<pad>` = 1 that is not
the blank (whole-project review, 2026-11-13). No torch here: the emissions are a
stand-in with the four tensor calls the readers make.
"""

import sys
import types

import pytest

from audit.ear import blank_id_of
from kashi_server.pipeline import alignment

# omniASR-CTC's head, shortened: pad "<s>" = 0 is the blank, "<pad>" = 1 is not.
OMNI_VOCAB = {"<s>": 0, "<pad>": 1, "</s>": 2, "<unk>": 3, "|": 4, "a": 5, "b": 6}
STAR = len(OMNI_VOCAB)  # ctc_forced_aligner appends a <star> column that wins every frame
FRAMES = [0, 5, 5, 0, 6, 0, 0, 4, 5, 0]  # argmax per frame over the real columns


class _Ids(list):
    def squeeze(self):
        return self

    def tolist(self):
        return list(self)


class _Emissions:
    """[frames x columns] log-probs; the star column is the loudest everywhere."""

    def __init__(self, rows: list[list[float]]):
        self.rows = rows

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.rows), len(self.rows[0])

    def __getitem__(self, key):
        _frames, cols = key
        return _Emissions([row[cols] for row in self.rows])

    def dim(self) -> int:
        return 2

    def argmax(self, dim: int = -1):
        assert dim == -1
        return _Ids(max(range(len(row)), key=row.__getitem__) for row in self.rows)


def _emissions() -> _Emissions:
    rows = []
    for best in FRAMES:
        row = [-9.0] * (STAR + 1)
        row[best] = -0.1
        row[STAR] = 0.0
        rows.append(row)
    return _Emissions(rows)


def _tokenizer(vocab: dict[str, int], pad_token_id: int):
    return types.SimpleNamespace(get_vocab=lambda: dict(vocab), pad_token_id=pad_token_id)


@pytest.fixture
def omni(monkeypatch):
    tokenizer = _tokenizer(OMNI_VOCAB, pad_token_id=0)
    model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (model, tokenizer))
    monkeypatch.setattr(alignment, "_emit",
                        lambda m, audio, exact_stride=False: (_emissions(), 20.0))
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", types.SimpleNamespace(
        load_audio=lambda path, dtype=None, device=None: object(),
        generate_emissions=lambda m, audio, batch_size=4: (_emissions(), 21),
    ))
    return tokenizer


def test_the_blank_is_the_aligners_own_on_every_production_shape_and_on_omni():
    # MMS, wav2vec2 English, the Turkish checkpoint: as before 2.32.0.
    assert alignment.ctc_blank_id(_tokenizer({"<blank>": 0, "<pad>": 1, "a": 4}, 1)) == 0
    assert alignment.ctc_blank_id(_tokenizer({"<pad>": 0, "<s>": 1, "|": 4}, 0)) == 0
    assert alignment.ctc_blank_id(_tokenizer({"[PAD]": 0, "<s>": 1, "|": 4}, 0)) == 0
    # omniASR-CTC: the old `vocab["<pad>"]` lookup read 1 here.
    assert alignment.ctc_blank_id(_tokenizer(OMNI_VOCAB, 0)) == 0


def test_the_sheet_shifts_ear_drops_omnis_blank(omni):
    heard = alignment.heard_tokens(alignment.Path("x.wav"), "eng")
    assert heard == [
        {"text": "a", "start_ms": 20},
        {"text": "b", "start_ms": 80},
        {"text": "|", "start_ms": 140},
        {"text": "a", "start_ms": 160},
    ]


def test_the_by_ear_transcript_drops_omnis_blank(omni):
    assert alignment.transcribe(alignment.Path("x.wav"), "omni") == "ab a"


def test_the_audits_ear_takes_the_same_blank(omni):
    assert blank_id_of(omni) == 0
    assert blank_id_of(_tokenizer({"<blank>": 0, "<pad>": 1, "a": 4}, 1)) == 0
    assert blank_id_of(_tokenizer({"[PAD]": 0, "<s>": 1, "|": 4}, 0)) == 0
