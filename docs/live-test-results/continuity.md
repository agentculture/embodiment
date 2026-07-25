# Continuity across processes

**Date:** 2026-07-25 · **Host:** `examples/greenhouse.py` · **Rig:** see [README](README.md)

The claim under test is issue #2's: *"an embodiment without memory is a
sequence of awakenings."* So: does a second process, with no shared state
except a durable store, actually carry the first one's knowledge?

## Museless baseline

Two separate `uv run` invocations against one `--home`.

**Run 1** — 28s, exit `finished`

```text
utterance: "New plant card - name: Marlow; sensor: s-fig-01; water below: 30%
            moisture. It is the fig by the north window. Check it in and log the visit."

recalled nothing — this greenhouse has no history yet.
  read_sensor(sensor='s-fig-01')  -> s-fig-01 reads 42% moisture
  log_care(plant='Marlow', ...)   -> logged: checked Marlow
  finish(...)                     -> visit closed
summary: Marlow (s-fig-01, water below 30%): checked, moisture at 42%, no watering needed.
remembered: embodiment-task-visit-1 (links: none)
```

**Run 2** — separate process, 59s, exit `finished`

```text
utterance: "Does Marlow need water today?"        <- names no sensor, no threshold

recalled 1 prior record:
  [embodiment-task-visit-1] Marlow (s-fig-01, water below 30%): checked, moisture at 42% ...
  read_sensor(sensor='s-fig-01')  -> s-fig-01 reads 22% moisture
  log_care(plant='Marlow', action='watered', note='moisture at 22%, below 30% threshold')
  finish(...)                     -> visit closed
summary: Marlow (s-fig-01, water below 30%): watered, moisture was 22%.
remembered: embodiment-task-visit-2 (links: embodiment-task-visit-1)
```

### Why this is load-bearing rather than decorative

The second utterance never mentions `s-fig-01` or the 30% rule, and neither is
in the code. Three independent things had to come from memory:

1. **which sensor to read** — it called `read_sensor(s-fig-01)` unprompted;
2. **the threshold** — it watered at 22% *because* 22 < 30, a rule stated once,
   in a previous process;
3. **the link** — visit-2's durable record `links` back to visit-1's.

The control experiment is what makes this evidence: run the same second
utterance against an **empty** store and the assistant refuses, saying it has no
plant card and asking for one. It is not re-deriving the answer from the
question.

## With the advisory lane

Same first utterance, `--muse --identity Gwen`. 32s — **4s of muse overhead**.

```text
→ cortex: Check for any notes, logs, or unusual plant growth that might
          indicate the current objective.
Gwen: still working — 2 action(s); last: log_care
mind: cortex=...Qwen3.6-27B... muse=...Gemma-4-31B... identity=Gwen
```

`→ cortex:` is muse guidance reaching the actor. `Gwen:` is the presence beat
under identity framing — the speaker label is host-supplied, never inferred.

**The summary came out materially more complete:**

| | summary |
|---|---|
| museless | `Marlow (s-fig-01, water below 30%): checked, moisture at 42%, no watering needed.` |
| two-mind | `Plant: Marlow (fig by the north window). Sensor: s-fig-01. Water below: 30% moisture. Action: Checked moisture at 42%; no watering needed.` |

That matters more than it looks. **The durable record's text *is*
`TaskResult.summary`** — so what survives to the next process is a
prompt-quality question, not a storage one, and a muse that improves summaries
improves memory. This is a single observation, not a measured effect.

## What the runs taught the demo

Two findings went into the demo's own system prompt rather than a comment,
because they are prompt-design lessons a copying app author would otherwise
rediscover:

- The cortex answered *today's* question with *yesterday's* sensor reading
  until told explicitly that a recalled memory is a **past visit, never a
  present reading**.
- Because the record is the summary, the prompt has to say what a summary must
  restate — otherwise the next process inherits a sentence that reads well and
  carries nothing.

## Caveat

The live assertions in `tests/test_demo_greenhouse.py` pin the deterministic
chain hard (record written → record recalled → link present) and the
model-shaped part softly. Pinning a thinking model's exact tool trajectory
would measure its mood.
