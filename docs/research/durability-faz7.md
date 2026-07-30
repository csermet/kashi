# Durability tour — Faz 7 P4

**Status:** not run yet. Fill the Result column in place and commit; anything
that fails becomes either a 0.19.x fix or a documented limitation.

**Build under test:** overlay **0.21.0**, extension 0.1.12, server **0.16.1**
(pipeline 2.13.0). Effects must be on **hype** for every particle cell — that
is the only level where the layer exists at all.

Two things landed after this tour was written, and both change what the
particle cells will show:

- **Server-side effect selection** (pipeline 2.13.0). Effects fire far less
  often — a per-song cadence of roughly one per 9 s instead of one per tagged
  line. Cell B5 ("back-to-back bursts") is therefore much harder to provoke
  than it was; if you cannot make it pile up, that is the selection working,
  not the cell failing.
- **Archetype character** (overlay 0.21.0). Particles now have their own colour
  band, additive blending where the category is light, size-over-life and a
  flicker on shine. D1 is judged against these, not the flat pastel version.

## Why this exists

Faz 6.7 shipped the particle layer with a light durability pass: one
sleep/wake, one full-screen check. Two things changed since. Archetypes made
bursts longer-lived and gave them direction, so a burst can now still be on
screen when the next one starts. And box size became a user setting, which
means the geometry the layer aims at is no longer a constant. Both are the
kind of change that behaves perfectly in a unit test and badly on a real
desktop for an hour.

The cells below are the ones no test on this machine can answer.

## A. Display changes

The overlay saves its position and restores it against a visibility check, so
anything that changes the desktop's shape is a real risk.

| # | Cell | What to do | Expect | Result |
|---|---|---|---|---|
| A1 | Resolution change while running | Change the monitor's resolution with Kashi open | Box stays on screen, still readable, not clipped or half off-edge | |
| A2 | Scaling change (Windows) | 100% → 150% → back | Box does not creep or resize; text stays crisp, not blurry | |
| A3 | Monitor unplugged | Park the box on a second monitor, unplug it | Box comes back on the remaining screen rather than vanishing off-desktop | |
| A4 | Laptop lid / dock (Mac) | Dock and undock | Same as A3 | |
| A5 | Restart after a display change | Quit, change resolution, start again | Restores somewhere visible | |

## B. Sleep, wake and long runs

| # | Cell | What to do | Expect | Result |
|---|---|---|---|---|
| B1 | Sleep/wake, then an fx word | Sleep the machine mid-song, wake, let an fx word land | Particles fire; no crash, no frozen box | |
| B2 | Mac 20-minute soak | Hype on, archetypes firing, leave it 20 min | Memory flat within ±5 MB; no slow climb | |
| B3 | Windows 20-minute soak | Same | Same | |
| B4 | Idle cost | Song paused, no fx, 5 min | CPU back to idle — the ticker must stop, not spin on an empty scene | |
| B5 | Back-to-back bursts | A song with many fx words in a row (poison/love lines are longest) | No pile-up, no stutter; if the budget line appears in the log that is fine and expected | |

## C. YouTube Music full screen

The overlay claims to stay visible above full-screen content and to stay
click-through. Both are platform behaviour, not ours.

| # | Cell | What to do | Expect | Result |
|---|---|---|---|---|
| C1 | YTM full screen, Windows | Full-screen the player | Box still visible and on top | |
| C2 | YTM full screen, Mac | Same | Same | |
| C3 | Click-through in full screen | Click where the box is | The click reaches YTM, not the overlay | |
| C4 | Particles in full screen | Let an fx word land while full screen | Particles draw, and only around the box | |
| C5 | Another app full screen | Any other full-screen app | Documented behaviour either way — note what happens | |

## D. The new surfaces (0.18.0 / 0.19.0)

| # | Cell | What to do | Expect | Result |
|---|---|---|---|---|
| D1 | Each archetype, seen | Play the songs below — picked from the library because they carry the most words of each category | Each reads as its own thing. **Say which ones you dislike** — a disliked archetype gets revised or deleted, it does not ship on sufferance | |
| D2 | Box size × particles | Small and Large box, then trigger fx | Particles hug the box at every size; nothing cut off at the window edge | |
| D3 | Text size | Small and Large text | No overflow; long lines still wrap to at most 3 rows | |
| D4 | Medium/Medium is unchanged | Compare against 0.16.8 if you still have it | Identical — this is the promise the defaults make | |
| D5 | Size change mid-song | Change box size while a song plays | Box re-centres, lyrics keep flowing, no restart needed | |
| D6 | Size setting persists | Change size, quit, restart | Comes back at the chosen size, in a sensible position | |
| D7 | Size change during a burst | Change box size while particles are in flight | No crash; the next burst aims at the new box | |

### D1 — the shortest route to each archetype

Taken from the processed library: the track carrying the most words of that
category, so the effect fires several times per play rather than once.

| Archetype | Category | Song | Video id | fx words |
|---|---|---|---|---|
| burst | fire | Skillet — Burn It Down | `suk7JSx6qQs` | 32 |
| spark | electric | Thousand Foot Krutch — Untraveled Road | `JQplqa0z5zY` | 3 |
| fall | money | Dolly Parton — 9 to 5 | `xIIHdeneqX8` | 4 |
| fall | water | Hayri Yaşar Karagülle — Selam Trabzonum'a | `q8aKMedTkxg` | 4 |
| smoke | poison | Ava Max — Sweet but Psycho | `ttznokjeSnw` | 3 |
| twinkle | shine | Rihanna — Towards The Sun | `G3jq77_P-cA` | 8 |
| drift | love | Pitbull — Hey Baby (Drop It to the Floor) | `IL74nTSQxds` | 42 |

Two notes from the same query. **`explosion` does not occur anywhere in the
library** — the burst archetype reaches the screen through `fire` alone, so if
burst disappoints, that is the tag to judge it by. (The category itself is
fine: 13 English keywords and the highest intensity in the lexicon, so it wins
its line whenever it does appear. These songs simply do not sing about
explosions.) And `love` dominates
everything at 246 words across the library against `electric`'s 4, so drift is
the archetype that will be seen most and deserves the strictest eye.

## E. Diagnostics (P1 acceptance)

| # | Cell | What to do | Expect | Result |
|---|---|---|---|---|
| E1 | Drag the progress bar | Drag it around a few times | Grafana → Field Diagnostics shows `user_seek`, and the **anomaly count does not rise** | |
| E2 | Two machines, one key | Run Windows and Mac against the same server | Machine inventory lists **two** rows, not one | |

## Reporting

For each failure, note: cell, what happened, and whether it repeats. Log files
help most for B and C — start the portable build from a terminal so the log is
in front of you.

Cells that pass on one platform and fail on the other are worth recording as
such; several of these are platform behaviour we do not control, and knowing
which is which is the point of the tour.
