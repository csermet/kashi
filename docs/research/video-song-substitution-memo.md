# Video/Song Edit Substitution — Evaluation Memo (Faz 6 P7)

**Status:** analysis only — no implementation. Input for the Faz 6.5 scope
decision.

## The problem (field: Sinsirella, wUjSOU0p6f8)

For YTM **video** entries the browser may play a long video edit (451 s)
while yt-dlp fetches the **song** stream for the same id (216 s) — YouTube's
music clients substitute streams server-side. A document timed to audio the
browser never plays is confident nonsense. Since 2.4.2 the pipeline fails
honest when `hints.duration_ms` and the downloadable audio disagree by >30 s
(`CLIENT_EDIT_MISMATCH_S`, both numbers + an exit path in the message).

## Candidate permanent fixes

1. **Client duration authority (cheap, incremental).** The extension already
   ships `duration_ms` with `track_changed`; 2.4.2 uses it as a gate. Next
   step would be using it as a *selector*: when the id yields multiple
   formats/edits, prefer the one matching the client's clock. Reality check:
   yt-dlp exposes formats of ONE edit per id — there is no "other edit" to
   pick. Verdict: no additional win beyond the 2.4.2 gate.
2. **Video→song id mapping.** Resolve the video id to its song counterpart
   (YTM exposes related "song" entries via the page/api the extension
   sees). Extension-side: read the watch-page metadata and send BOTH ids;
   server ingests the song id. Protocol change (additive field), MV3 work,
   and a YTM-internals dependency (ytm-scout research needed — brittle
   surface). This is the real fix candidate.
3. **BYO-audio escape (exists).** The 2.4.2 error message already points to
   `POST /v1/uploads` — the user uploads what they actually hear. Manual but
   universal.

## Recommendation

Keep the honest-fail + upload escape as the shipped behavior. If the class
keeps hurting in the field, pursue (2) in Faz 6.5 with a ytm-scout round on
how reliably the watch page exposes the paired song id; (1) is a dead end.
