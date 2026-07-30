-- Does a reprocessed document actually obey the selection rules?
--
-- Written for the pipeline 2.13.0 validation wave, kept because the same four
-- questions apply to any future retune of fx_select.py. Read-only.
--
--   kubectl exec -i -n devops postgresql-0 -c postgresql -- sh -c \
--     'PGPASSWORD=$(cat "$POSTGRES_POSTGRES_PASSWORD_FILE") psql -U postgres -d kashi' \
--     < apps/server/scripts/fx_select_acceptance.sql
--
-- Every row it prints is a FAILURE. Silence is the pass.

\echo '== 1. a SELECTED document stays inside the song cadence ============'
-- Only documents that carry the stamp are judged. An unstamped one has simply
-- not been reprocessed yet — most of the archive is in that state on purpose,
-- and counting it as a failure would drown the real ones (it did, first run).
-- The cap is clamp(round(duration_s / 9), 4, 24), and it is measured in
-- GESTURES: from density/1.1 a repeated word on one line ("music, music,
-- music") is one effect to the eye and emits several entries, so counting rows
-- would flag every run-bearing document as a violation.
WITH g AS (
  SELECT pt.source_id,
         count(DISTINCT ((e->>'line') || ':' || (e->>'tag'))) AS gestures,
         (SELECT max((l->>'end_ms')::int)
            FROM jsonb_array_elements(pt.document->'lines') l) AS dur_ms,
         pt.document->'fx'->>'select' AS plan
  FROM processed_tracks pt, LATERAL jsonb_array_elements(pt.document->'fx'->'words') e
  WHERE pt.document->'fx'->>'select' IS NOT NULL
  GROUP BY 1, 3, 4
)
SELECT source_id, plan, gestures, round(dur_ms / 1000.0) AS dur_s,
       greatest(4, least(24, round(dur_ms / 9000.0))) AS cap
FROM g
WHERE gestures > greatest(4, least(24, round(dur_ms / 9000.0)));

\echo '-- how far the wave has got (informational) ------------------------'
SELECT count(*) FILTER (WHERE document->'fx'->>'select' IS NOT NULL) AS selected,
       count(*) FILTER (WHERE document ? 'fx' AND document->'fx'->>'select' IS NULL) AS still_legacy
FROM processed_tracks;

\echo '== 2. two GESTURES on one line are never adjacent =================='
-- At least two plain words between them (index gap >= 3). Measured between
-- gestures, not words: the members of one repeated-word run sit right next to
-- each other by definition, and that is the gesture, not a violation.
WITH w AS (
  SELECT source_id, (e->>'line')::int AS li, (e->>'word')::int AS wi, e->>'tag' AS tag
  FROM processed_tracks pt, LATERAL jsonb_array_elements(pt.document->'fx'->'words') e
  WHERE pt.document->'fx'->>'select' IS NOT NULL
), runs AS (
  SELECT source_id, li, tag, min(wi) AS first_wi, max(wi) AS last_wi
  FROM w GROUP BY 1, 2, 3
), gaps AS (
  SELECT source_id, li, first_wi,
         first_wi - lag(last_wi) OVER (PARTITION BY source_id, li ORDER BY first_wi) AS gap
  FROM runs
)
SELECT source_id, li AS line, first_wi AS word, gap
FROM gaps WHERE gap IS NOT NULL AND gap < 3;

\echo '== 3. a line never carries more than its length allows ============='
-- <=5 words -> 1, <=10 -> 2, else 3.
WITH per_line AS (
  SELECT pt.source_id, (e->>'line')::int AS li,
         count(DISTINCT e->>'tag') AS hits,
         jsonb_array_length(pt.document->'lines'->((e->>'line')::int)->'words') AS words
  FROM processed_tracks pt, LATERAL jsonb_array_elements(pt.document->'fx'->'words') e
  WHERE pt.document->'fx'->>'select' IS NOT NULL
  GROUP BY 1, 2, 4
)
SELECT source_id, li AS line, words, hits
FROM per_line
WHERE hits > CASE WHEN words <= 5 THEN 1 WHEN words <= 10 THEN 2 ELSE 3 END;

\echo '== 4. no two effects land within 700 ms ============================'
-- Across lines, not just within one — back-to-back short lines were the hole
-- the per-line rule could not see.
-- Timed at each gesture's LAST member, the way the sweep does it: a run is
-- deliberately sung fast, so timing its members individually would report the
-- gesture as a violation of itself.
WITH t AS (
  SELECT pt.source_id,
         max(coalesce(
           (pt.document->'lines'->((e->>'line')::int)->'words'->((e->>'word')::int)->>'start_ms')::int,
           (pt.document->'lines'->((e->>'line')::int)->>'start_ms')::int
         )) AS at_ms
  FROM processed_tracks pt, LATERAL jsonb_array_elements(pt.document->'fx'->'words') e
  WHERE pt.document->'fx'->>'select' IS NOT NULL
  GROUP BY pt.source_id, (e->>'line')::int, e->>'tag'
), gaps AS (
  SELECT source_id, at_ms, at_ms - lag(at_ms) OVER (PARTITION BY source_id ORDER BY at_ms) AS gap
  FROM t
)
SELECT source_id, at_ms, gap FROM gaps WHERE gap IS NOT NULL AND gap < 700;

\echo '== summary (informational, not a failure list) ====================='
-- Chorus coverage cannot be asserted from the document: the candidates that
-- were NOT selected are no longer stored, so a chorus with no effect may
-- simply have had no tagged word in it. Printed to be read, not to gate.
SELECT pt.source_id,
       jsonb_array_length(pt.document->'fx'->'words') AS kept,
       (SELECT count(*) FROM jsonb_array_elements(pt.document->'sections') s
        WHERE s->>'type' = 'chorus') AS choruses,
       (SELECT count(*) FROM jsonb_array_elements(pt.document->'sections') s
        WHERE s->>'type' = 'chorus'
          AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(pt.document->'fx'->'words') e
            WHERE ((pt.document->'lines'->((e->>'line')::int)->>'start_ms')::int
                 + (pt.document->'lines'->((e->>'line')::int)->>'end_ms')::int) / 2
                  BETWEEN (s->>'start_ms')::int AND (s->>'end_ms')::int
          )) AS choruses_with_an_effect
FROM processed_tracks pt
WHERE pt.document->'fx'->>'select' IS NOT NULL
ORDER BY 1;

\echo '== 5. every repeat of a line fires the SAME pattern (2.14.0+) ======'
-- The field complaint density/1.1 exists for: identical lines that chose
-- different words on different repeats. Grouping is by the rendered TEXT,
-- lowercased — close enough for an audit even though the selector normalizes
-- more thoroughly.
WITH repeats AS (
  SELECT pt.source_id, lower(l->>'text') AS txt, (i - 1) AS li
  FROM processed_tracks pt,
       LATERAL jsonb_array_elements(pt.document->'lines') WITH ORDINALITY a(l, i)
  WHERE pt.document->'fx'->>'select' = 'density/1.1'
), fired AS (
  SELECT pt.source_id, (e->>'line')::int AS li,
         string_agg(DISTINCT e->>'word', ',' ORDER BY e->>'word') AS pattern
  FROM processed_tracks pt, LATERAL jsonb_array_elements(pt.document->'fx'->'words') e
  WHERE pt.document->'fx'->>'select' = 'density/1.1'
  GROUP BY 1, 2
)
SELECT r.source_id, left(r.txt, 30) AS line_start,
       count(DISTINCT f.pattern) AS distinct_patterns,
       array_agg(DISTINCT f.pattern) AS patterns
FROM repeats r JOIN fired f ON f.source_id = r.source_id AND f.li = r.li
GROUP BY 1, 2
HAVING count(DISTINCT f.pattern) > 1;
