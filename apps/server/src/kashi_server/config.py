"""Runtime configuration from environment variables (pydantic-settings).

Field names map to env vars case-insensitively (database_url <- DATABASE_URL,
the house convention). `schema_path` also accepts KASHI_SCHEMA_PATH, which the
Docker image sets to the baked-in schema copy.
"""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings


def _default_schema_path() -> Path:
    """Walk up towards a repo checkout; fall back to the image's baked-in copy.
    A fixed parents[N] index crashed at import inside the container, where the
    module sits only 3 levels deep (/app/src/kashi_server/config.py)."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "packages" / "schemas" / "processed-track.v1.schema.json"
        if candidate.exists():
            return candidate
    return Path("/app/schemas/processed-track.v1.schema.json")


class AlignerChoice(BaseModel):
    """One language's aligner: the checkpoint AND the text form it wants.

    The two travel together on purpose. `romanize` is not a preference, it is
    a property of the checkpoint's vocabulary — MMS learned romanized Latin,
    mpoyraz's Turkish model learned ç ğ ı ö ş ü. Faz 8.1 measured what happens
    when they come apart: with `romanize=True` uroman also strips punctuation,
    so turning it off for the Turkish model made the first comma or ♪ abort
    alignment and five of ten songs vanished. Two parallel dicts would let an
    operator pick a checkpoint and forget its flag; one object cannot.

    A bare string is accepted as shorthand for "this checkpoint, inherit the
    global romanize setting":  {"eng": "jonatasgrosman/wav2vec2-xls-r-1b-english"}
    """

    checkpoint: str = Field(min_length=1)
    romanize: bool | None = None  # None -> fall back to settings.align_romanize
    offset_ms: int | None = None  # None -> fall back to settings.align_offset_ms

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_checkpoint(cls, value):
        return {"checkpoint": value} if isinstance(value, str) else value


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://kashi:kashi@localhost:5432/kashi"
    admin_api_key: str | None = None
    data_dir: Path = Path("/scratch")
    model_cache_dir: Path = Path("/models")  # exported as HF_HOME by the worker
    # Pipeline 2.0.0 defaults — every one of these is MEASURED, not assumed
    # (9-config / 79-song matrix + field cases, 2026-07-11/12; see
    # docs/research/hizalama-v2-benchmark-2026-07.md).
    separation_mode: Literal["off", "second_pass", "always"] = "always"
    # lrclib-anchored windowed alignment (P3): a CTC lock loss cannot
    # propagate past a window edge (the dominant field failure mode).
    windowed_alignment: bool = True
    # WHICH forced aligner. A seam, not a knob to turn casually — every value
    # here changes word timings wholesale (Faz 8 P-B1).
    #
    # It exists because the default is a licence dead end: the MMS-300m
    # checkpoint and Meta's facebook/mms-300m under it are BOTH CC-BY-NC-4.0,
    # so the shipped chain cannot go into a paid product at all — the
    # wrapper's own README says to use a different model commercially. That
    # makes swapping the aligner a certainty rather than a maybe, and a
    # constant buried in a module is the wrong place for a certainty.
    #
    # This is the FALLBACK for languages `align_models` (below) does not
    # name; per-language routing lives there.
    #
    # Anything Hugging Face's `load_alignment_model` accepts. The document
    # records what actually ran in `alignment.method`, so a swap is visible
    # in the archive rather than silent.
    # Detail: docs/research/hizalama-yontem-adaylari-2026-08.md
    align_model: str = "MahmoudAshraf/mms-300m-1130-forced-aligner"
    # uroman romanization before alignment. REQUIRED by MMS, whose vocabulary
    # is romanized Latin. A model trained on the language's own alphabet wants
    # the opposite: mpoyraz's Turkish vocab carries ç ğ ı ö ş ü natively, and
    # romanizing first would hand it "cgiosu" — a different phoneme set than
    # the one it learned. Measuring such a model with this left on understates
    # it, so the flag rides with the checkpoint.
    align_romanize: bool = True
    # Constant shift applied to every aligned word and line, in ms (Faz 9 P1).
    # Negative moves timings EARLIER. 0 by default: a value here changes every
    # timing the pipeline produces, so it is set per language after being
    # measured, never inherited.
    #
    # It exists because alignment error on SINGING is not centred on zero.
    # Measured on JamendoLyrics English (full-precision human annotation, 5693
    # words, 20 songs): 76% of words are marked LATE, median +80 ms, and all
    # twenty songs carry it. The mechanism was predicted before it was
    # measured — a sung note's onset lands on the syllable's VOWEL while the
    # written word starts on a consonant, so a CTC model hears the vowel and
    # reports the start about one consonant late.
    #
    # It is a property of singing rather than of a checkpoint: MMS-300m and
    # jonatasgrosman's XLS-R 1B, with different separators, produce the same
    # 76% / +80 ms / 20-of-20. Still measured per language before shipping,
    # because "we expect it to transfer" is how unmeasured claims get in.
    # Tool: `python -m benchmarks.lateness <run>.json` (leave-one-song-out).
    align_offset_ms: int = 0
    # PER-LANGUAGE routing over the three fields above (Faz 8.1, Faz 9 P1). Empty by
    # default: with no entry for a job's language, `align_model` /
    # `align_romanize` answer exactly as they did before, so every existing
    # measurement stays valid.
    #
    # It exists because the licence-clean chain is no longer ONE checkpoint.
    # Faz 8.1 measured a permissive replacement per language and they are
    # different models: English wants jonatasgrosman/wav2vec2-xls-r-1b-english
    # (Apache-2.0, PCO 0.8789 — it BEAT the CC-BY-NC MMS default), Turkish
    # wants mpoyraz/wav2vec2-xls-r-300m-cv7-turkish (CC-BY-4.0, 0.930 vs 0.938
    # = no statistical difference) with romanize OFF, because its vocabulary
    # is Turkish rather than romanized Latin.
    #
    # Keys are language codes as the aligner sees them (ISO-639-3: "eng",
    # "tur"); "en"/"tr" are accepted and normalized, since a 2-letter key that
    # silently never matched is the exact failure this project keeps paying
    # for. Set from the environment as JSON:
    #   ALIGN_MODELS='{"eng":"jonatasgrosman/wav2vec2-xls-r-1b-english",
    #                  "tur":{"checkpoint":"mpoyraz/wav2vec2-xls-r-300m-cv7-turkish",
    #                         "romanize":false}}'
    # Operational note: the worker caches models by name, so a mixed-language
    # queue holds every configured checkpoint in memory at once.
    # Detail: docs/research/lisans-temiz-zincir-2026-08.md
    align_models: dict[str, AlignerChoice] = {}
    # audio-separator registry filename. Kim MelBand: best measured PCO/MAE of
    # all candidates at ~2.1x realtime on the worker (BS-RoFormer quality at a
    # third of its cost); higher-SDR models measured WORSE for alignment.
    separation_model_filename: str = "mel_band_roformer_kim_ft_unwa.ckpt"
    # Fraction of the ORIGINAL mix folded back into the vocal stem. Measured
    # HARMFUL on average (dilutes the clean-vocal advantage; ~10x MedAE) —
    # kept only as an escape hatch. 0 disables the pass.
    separation_mixback: float = 0.0
    # Nightcore auto-detection (Faz 4): titles carrying nightcore/sped-up
    # markers trigger a speed-factor probe against lrclib. Explicit ingest
    # options bypass this switch.
    nightcore_detection: bool = True
    lrclib_base_url: str = "https://lrclib.net"
    max_track_duration_s: int = 1200
    # BYO-audio staging (Faz 5 P4): multipart cap enforced while streaming
    # (64 MB covers ~1h of 128kbps audio, far past the duration cap anyway);
    # orphaned rows — job never ran or was canceled — are swept after the TTL.
    upload_max_bytes: int = 64 * 1024 * 1024
    upload_ttl_hours: int = 24
    # lrclib contribute-back (Faz 5 P6): BOTH flags must be flipped for a
    # real publish — the feature defaults hard-off, and even when enabled,
    # dry-run only LOGS the YAML until the operator is sure.
    lrclib_publish_enabled: bool = False
    lrclib_publish_dry_run: bool = True
    # FX embedding layer (Faz 6 P3, `semantics` extra): line-theme tagging
    # via multilingual-e5-small. The keyword/stem layer is dependency-free
    # and ALWAYS runs; this flag only gates the model.
    # DEFAULT OFF since pipeline 2.9.0: the P4 calibration (200 labeled
    # archive lines) showed the layer's verdicts are ~half wrong at EVERY
    # threshold — E5 prototype-centroid cosines do not separate right from
    # wrong on lyric lines. A wrong theme is worse than no theme (DG6), so
    # the layer is opt-in for experimentation only. Full data:
    # docs/research/embed-threshold-calibration-2026-07.md
    fx_embeddings: bool = False
    # Structure v2 (Faz 6.5 P6): librosa Laplacian segmentation → "chorus"
    # sections beside the energy "high" blocks. Zero extra dependencies,
    # deterministic; default off until the canary wave proves it in the
    # field (BAD GIRL is the acceptance case).
    structure_sections: bool = False
    # Field diagnostics (Faz 6.7 P1). Default ON, unlike the publish flags:
    # nothing arrives unless a client is configured with this server's URL and
    # one of its API keys, so the data is the operator's own by construction.
    # Set false to make the endpoint refuse without redeploying clients.
    telemetry_enabled: bool = True
    # Retention runs on the SERVER's clock (see purge_old_telemetry): a client
    # with a wrong clock must not be able to pin rows forever or erase them.
    telemetry_retention_days: int = Field(default=30, ge=1)
    queue_depth_limit: int = 200
    worker_poll_interval_s: float = 2.0
    retry_delays_s: list[int] = [60, 300, 900]
    lease_ttl_s: int = 600
    bgutil_pot_provider_url: str | None = None
    metrics_port: int = 9090
    schema_path: Path = Field(
        default_factory=_default_schema_path,
        validation_alias=AliasChoices("KASHI_SCHEMA_PATH", "SCHEMA_PATH"),
    )

    @field_validator("align_models")
    @classmethod
    def _normalize_language_keys(cls, value: dict) -> dict:
        """Key the table the way the aligner is called ("en" -> "eng").

        Raises on two keys that mean the same language: a config saying both
        "en" and "eng" has no answer, and picking one silently is how a job
        ends up on a checkpoint nobody chose.
        """
        from kashi_server.pipeline.langid import to_iso639_3

        normalized: dict[str, AlignerChoice] = {}
        for key, choice in value.items():
            code = to_iso639_3(key)
            if code in normalized:
                raise ValueError(f"align_models has two entries for {code!r} (one is {key!r})")
            normalized[code] = choice
        return normalized


settings = Settings()
