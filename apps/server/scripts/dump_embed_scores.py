"""Dump per-line embedding scores from the archive — EMBED_THRESHOLD
calibration input (Faz 6.5 P4).

For every processed document: re-detect the language, drop the lines the
keyword layer already claims (they never reach the embedding layer in
production), embed the rest exactly the way semantics.py does, and print
one TSV row per line with the best category and its cosine. The output gets
hand-labeled (~120-150 sampled lines) and swept 0.80-0.92 per language for
the precision/recall curve.

Never ships in the image. Run INSIDE an environment that has the
`semantics` extra + the model cache — e.g. the cluster worker pod:

    kubectl exec -i -n kashi-server deploy/kashi-worker -- \
        python3 - < scripts/dump_embed_scores.py > /tmp/embed-scores.tsv

Columns: source_id  language  line_index  best_tag  cosine  line_text
"""

import os
import sys

from sqlalchemy import create_engine, text

from kashi_server.pipeline.langid import detect_language
from kashi_server.pipeline.semantics import (
    _keyword_category,  # noqa: PLC2701 — calibration mirrors production exactly
    _tokens,  # noqa: PLC2701
    get_embedder,
    load_lexicon,
    normalize,
)


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    lexicon = load_lexicon()
    embedder = get_embedder(os.environ.get("HF_HOME"))

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT source_id, document FROM processed_tracks "
                "ORDER BY source_id"
            )
        ).fetchall()

    print("source_id\tlanguage\tline_index\tbest_tag\tcosine\tline_text")
    for source_id, doc in rows:
        lines = doc.get("lines") or []
        texts = [line.get("text") or "" for line in lines]
        if not texts:
            continue
        language = detect_language(" ".join(texts))
        candidates: list[tuple[int, str]] = []
        for index, line_text in enumerate(texts):
            tokens = _tokens(line_text)
            if not tokens:
                continue
            # Production sends a line to the embedder only when NO word in it
            # got a keyword tag (tag_words tracks lines_with_hits).
            if any(_keyword_category(token, lexicon) for token in tokens):
                continue
            candidates.append((index, line_text))
        if not candidates:
            continue
        # classify() applies the threshold; for calibration we need raw
        # scores — use threshold 0 and read the winner + cosine per line.
        # (classify returns tags only, so recompute the sims the same way.)
        import numpy as np

        vecs = embedder._model.encode(  # noqa: SLF001 — calibration tool
            # Mirror classify() exactly: normalized text, "query: " prefix.
            [f"query: {normalize(t)}" for _, t in candidates],
            normalize_embeddings=True,
        )
        sims = np.asarray(vecs) @ embedder._centroids.T  # noqa: SLF001
        for (index, line_text), row in zip(candidates, sims, strict=True):
            best = int(np.argmax(row))
            clean = line_text.replace("\t", " ").replace("\n", " ")
            print(
                f"{source_id}\t{language}\t{index}\t"
                f"{embedder._ids[best]}\t{float(row[best]):.4f}\t{clean}"  # noqa: SLF001
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
