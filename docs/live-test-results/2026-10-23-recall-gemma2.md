# Recall measured: keyword versus gemma2-embed, threshold, volume ceiling, arrival (t18)

Plan task `t18` of embodiment#94, run 2026-10-23 on the rig. Produced by
`scripts/measure-recall.py`. **Aggregates only**: this repo is public and the
store is one person's memory, so no record text, query text, label or record id
appears here. The labelled set, the per-query results and the filler live in a
private scratch directory outside the repo.

## Read this first

- **n is small.** The labelled set is 24 target records (17 `explicit-ask`
  and 7 `session-summary`, every active record in the store) with 2 queries each
  (48 labelled queries) and 44 unrelated queries. Every rate below is a count
  over those n; a one-query difference moves a rate by 2 points.
- **One person's store, one gateway.** The calibration is on one operator's
  Hebrew memory, with queries written by the `senses` role (Gemma-4-26B), not by
  a person speaking. "Reworded" queries were asked to avoid the record's content
  words; how hard that is depends on the writer. None of this is a measurement
  of real use.
- **Unrelated queries are easy negatives.** They are generic, impersonal
  general-knowledge questions. They are not near-misses and not web-derived
  newspaper text, which is the case the threshold was meant to keep out (spec
  c52). A false-positive rate here is a floor, not a ceiling.
- **The threshold was chosen on the data it is scored on.** The two-fold
  cross-check below is the only out-of-sample view, and it is n = 24 per fold.
- Recall ran on copies of the store with `reinforce=False`. The live store was
  not touched.

## Method

| Arm | Path | Notes |
| --- | --- | --- |
| keyword + d5 | `RoomMemory.recall(mode="keyword")`, then the daemon's exact-substring fallback over `_fallback_terms` | the daemon's own helpers, 0.25 s budget, as `_recall` ran before t16 for a lexical store |
| semantic | `RoomMemory.recall(mode="semantic")`, `EmbedderClient(model="gemma2-embed")`, cached vectors from `backfill_tick` | one query embed (`Retrieval-query`), records embedded as `Retrieval-document` |

Hit@k means the target record is among the first k returned; a query with no
record returned is a miss. Semantic per-record cosines were captured at a
near-zero threshold and the threshold sweep was applied offline to those
cosines. The thresholded numbers further down were then re-measured live.
The daemon does not run the d5 fallback under semantic recall (t16), so
"keyword + d5" is the before arm and "semantic" runs without it.

## hit@5 and hit@1

n = 24 targets per kind. Keyword latency is in the next section.

| Arm | Close hit@5 | Close hit@1 | Reworded hit@5 | Reworded hit@1 |
| --- | --- | --- | --- | --- |
| keyword + d5 | 18/24 | 8/24 | 1/24 | 1/24 |
| semantic, default threshold 0.78 | 24/24 | 18/24 | 3/24 | 2/24 |
| semantic, calibrated 0.715 | 24/24 | 18/24 | 15/24 | 8/24 |
| semantic, no threshold (ranking only) | 24/24 | 18/24 | 21/24 | 10/24 |

Misses of the keyword arm are mostly empty answers: 3 of 24 close and 20 of 24
reworded queries returned nothing at all. Under the shipped default 0.78,
semantic recall is empty for 19 of 24 reworded queries, so the default turns
away most paraphrases that the embedder can in fact rank.

Semantic is not worse than keyword on any cell. Close queries share words with
the record, so keyword recall finds most of them and semantic finds all.
Reworded queries are where keyword + d5 is blind and where the threshold
decides the outcome.

## Threshold (the calibrated value)

Sweep over the 48 labelled queries (hit counts at 5 and 1) and the 44 unrelated
queries (false positive = at least one record returned):

| Threshold | hit@5 of 48 | hit@1 of 48 | false positives of 44 |
| --- | --- | --- | --- |
| 0.62 | 45 | 28 | 40 |
| 0.66 | 45 | 28 | 25 |
| 0.68 | 44 | 28 | 13 |
| 0.70 | 40 | 27 | 7 |
| 0.715 | 39 | 26 | 2 |
| 0.72 | 34 | 22 | 1 |
| 0.74 | 31 | 22 | 1 |
| 0.76 | 28 | 21 | 0 |
| 0.78 (default) | 27 | 20 | 0 |
| 0.82 | 24 | 18 | 0 |
| 0.86 | 13 | 11 | 0 |

- **Calibrated value: 0.715** (0.7147 as selected): the highest hit@5 subject to
  a false-positive rate of at most 5% on unrelated queries. At it: hit@5 39/48,
  hit@1 26/48, false positives 2/44 (4.5%). Not applied: `memory.py` is not this
  task's file; the default stays 0.78 and `SIMILARITY_THRESHOLD_CALIBRATED`
  stays False until the owner decides.
- **The default 0.78 gave 27/48 hit@5 and 0/44 false positives.** It is
  conservative: it cost 12 of the 39 hits available at 0.715, almost all
  reworded queries.
- **The curve has a cliff between 0.70 and 0.72.** False positives fall from 7
  to 1 and hits from 40 to 34 across a 0.02 step. A threshold in that range is
  sensitive to the embedder's version and to the query writer.
- **Cross-check, two folds by target record (n = 24 labelled and 22 unrelated
  per fold):** thresholds 0.713 and 0.715 were chosen on one half and scored on
  the other. Held out: hit@5 19/24 and 20/24, false positives 2/22 and 1/22.
  One fold exceeds the 5% target (2 of 22 is 9%). The calibrated value is
  therefore not shown to hold out of sample at 5%.
- Semantic recall re-measured live at 0.715 (n = 276 calls): 2 of 44 unrelated
  queries returned a record; at 0.78, 0 of 44.

## Latency

Recall-call latency, measured around `RoomMemory.recall` on the 24-record store
(the real store). Semantic includes the query embed over the proxied
`gemma2-embed` lane; n counts calls (labelled and unrelated queries, 3 passes).

| Arm | n | median | p90 | max |
| --- | --- | --- | --- | --- |
| keyword + d5 | 144 | 0.8 ms | 2.3 ms | 3.3 ms |
| semantic, threshold 0.715 | 276 | 162.0 ms | 171.5 ms | 241.4 ms |
| semantic, threshold 0.78 | 276 | 162.3 ms | 169.8 ms | 194.5 ms |
| the query embed alone | 30 | 156.4 ms | 161.1 ms | 163.7 ms |

The query embed is the clock. It is about 156 ms of a 250 ms deadline, a network
quantity (a proxied lane on another machine), and it does not change with the
store. Keyword recall is three orders of magnitude cheaper and does not touch
the network. Semantic recall left 241 ms at worst on this store, and no call in
the 276 exceeded 0.25 s; one came within 9 ms.

## Recall-arrival distribution

How long the recall takes to land once the turn asks for it. This is the
recall call only. The turn-start offset (transcription, the floor) is not in
it and was not measured here.

| Arm | n | median | p90 | max |
| --- | --- | --- | --- | --- |
| semantic, 24 records | 276 | 162.0 ms | 171.5 ms | 241.4 ms |
| keyword + d5, 24 records | 144 | 0.8 ms | 2.3 ms | 3.3 ms |

The arrival window for the follow-up rule should be derived from these figures
plus the volume scaling below, not from the 0.25 s constant. On this rig a
24-record semantic recall lands about 0.16 to 0.17 s after it starts.

## Volume ceiling

Stores of 25, 200, 400, 600, 800 and 1000 active records: the real 24 plus
synthetic filler written by the `senses` role (made-up Hebrew facts, never real
text). Every record was embedded, so the cache was complete. Each size ran the
same 92 probe queries (48 labelled and 44 unrelated) at threshold 0.78 with a
30 s deadline, so no call was censored by the deadline being measured. The 25,
200 and 1000 sizes used 1 pass (n = 92 calls) and the 400, 600 and 800 sizes 2
passes (n = 184).

| Active records | n | median | p90 | max | calls over 0.25 s | store enumeration alone |
| --- | --- | --- | --- | --- | --- | --- |
| 25 | 92 | 159.4 ms | 166.3 ms | 177.3 ms | 0 | 1.1 ms |
| 200 | 92 | 192.4 ms | 195.2 ms | 229.3 ms | 0 | 5.0 ms |
| 400 | 184 | 207.0 ms | 221.9 ms | 246.8 ms | 0 | 9.8 ms |
| 600 | 184 | 231.6 ms | 239.7 ms | 281.8 ms | 12 | 14.7 ms |
| 800 | 184 | 263.0 ms | 287.6 ms | 950.8 ms | 184 | 19.9 ms |
| 1000 | 92 | 286.4 ms | 312.5 ms | 420.0 ms | 92 | 25.1 ms |

Scoring is linear in the number of records, about 0.13 ms per record (median
latency minus the 156 ms embed; the enumeration of the store is a fifth of it,
the remainder is the cosine pass in pure Python).

**The ceiling the derived deadline carries.** Both derived clocks are 0.25 s:
the recall deadline and the d2 head start. Against 0.25 s:

- p90 stays under 0.25 s up to **about 600 active records** (p90 239.7 ms), but
  at 600 records 12 of 184 calls (6.5%) already exceeded it.
- The median is under 0.25 s up to about 600 and over it by 800 records. At 800
  and 1000 every call exceeded it, so recall would be abandoned every time.
- A comfortable operating limit with margin for the embed's variance is
  **about 400 records** (max 246.8 ms, 0 of 184 over).
- The ceiling is a joint property of the embed latency and the store size. A
  slower or busier `gemma2-embed` lane lowers it one-for-one; the 800-record run
  also had a 950.8 ms outlier that is probably the lane, not the scoring.
- Ceiling over ingestion volume: the spool ingests newspaper issues and
  recordings into the same pool. At the sizes measured the ceiling is reached
  by a few hundred ingested records; a day of ingestion past that stops
  producing recalls in time, not stops erroring.
- **Hit@5 did not degrade with size** (27/48 at every size). That says nothing
  about precision at scale: the filler is generic and topically scattered, so
  it does not compete with the targets. A real pool of related newspaper text
  would, and was not measured.

Backfill cost, for sizing the background embed: 24 records took 4.1 s (n = 1
run), 200 took 38.9 s, 400 took 91.8 s, 600 took 147.4 s, 800 took 215.8 s and
1000 took 305.5 s, each a single run, sequential, about 0.2 to 0.3 s per
record.

## Lapse l3: transcription time against clip duration

The local `stt` lane (`ivrit-ai/whisper-large-v3-turbo`), synthetic audio:
generic Hebrew sentences synthesised by the `tts` role (24 kHz pcm16, wrapped as
wav), sent singly and joined in groups of 2 and 4 through
`/v1/audio/transcriptions`. n = 28 clips, 0 errors; clips run 1 to 8.8 s of
audio (median 2.6 s, p90 8.2 s).

| Quantity | median | p90 | max |
| --- | --- | --- | --- |
| transcription time | 945 ms | 1548 ms | 1764 ms |
| seconds per audio second | 0.314 | 0.497 | 0.614 |

Transcription ran at about 0.31 s per audio second (n = 28), well under real
time. The transcript text was not read or scored, so this is speed only, not
accuracy; synthetic speech from the same family of models is cleaner than a
person in a room. The first clip is not separated from the warm ones.

## Lapse l5: senses chat-completion latency

`senses` (Gemma-4-26B, proxied) with a short Hebrew question (one of the
unrelated queries), `max_tokens` 200, n = 30 sequential calls, 0 errors. Median
3983 ms, p90 4337 ms, max 5085 ms. This is whole-completion time to the token
limit, not time to first token; the daemon speaks sentence by sentence, so its
first-sentence latency is lower than this and is not measured here.

## What was not measured

- Precision with a realistic distractor pool (newspaper text, other recordings).
  Filler does not compete with targets, and the unrelated queries are easy.
- Anything with a human speaking: all queries are model-written text.
- Time from turn start to recall landing: only the recall call is timed.
- The cosine pass in the daemon process under load (the daemon runs the
  recall in a worker thread alongside the audio loop).
- Stores above 1000 records, and a vectorised cosine (the pass is pure Python;
  it is the part of the ceiling that code can move, the embed is not).
- Transcript accuracy for l3 and first-token latency for l5.

## Reproduce

Run on a copy of the store, with an output directory outside the repo:

```text
grant run --inject EMBODIMENT_GATEWAY_KEY=LOBES_GATEWAY_API_KEY -- \
  uv run python scripts/measure-recall.py gen --store COPY.jsonl --out DIR
... measure --store COPY.jsonl --out DIR
... volume  --store COPY.jsonl --out DIR --sizes 25,200,400,600,800,1000
... stt --out DIR
... senses --out DIR
```
