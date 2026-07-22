# Anti-AI-slop prose taxonomy — design

**Date:** 2026-07-17
**Branch:** `feat/anti-ai-slop`
**Scope:** backend only. No migration, no API change, no frontend change.

## Problem

RankForge already fights AI-sounding prose, but the knowledge is copy-pasted across
seven places and has drifted:

| Location | What it holds |
|---|---|
| `services/generation.py:110` | writer's banned-register list |
| `services/revise.py:96` | reviser's banned-register list |
| `services/revise.py:514` | critic prompt's (shorter) copy |
| `services/revise.py:912` | `_TELL_INSTRUCTION["ai_vocabulary"]` copy |
| `services/scoring.py:28` | `_AI_WORDS` regex tuple |
| `services/scoring.py:578` | readability judge prompt's copy |
| `services/linkedin_gen.py:92` | LinkedIn generator's (incomplete) copy |

The lists no longer match. Adding one pattern today means seven edits, so in practice
they diverge further with each change.

Coverage is also narrower than it should be. The current taxonomy catches ~40 register
words and ~9 formulaic constructions. It misses weasel attribution, importance puffery,
faux-insight setups, throat-clearing openers, rhetorical setups, superficial `-ing`
analysis, negative listing, dramatic fragmentation, and summary-recap endings. Weasel
attribution ("studies show", "experts agree") is the costliest gap: it works directly
against the GEO citability signal the product already scores.

The reference taxonomy is the `no-ai-slop` skill
(<https://github.com/petergyang/no-ai-slop>, MIT), which catalogues 20+ patterns plus a
governing principle RankForge lacks: **make the minimum effective edit** — remove slop
without flattening the prose around it.

## Goals

1. One source of truth for the prose taxonomy; adding a pattern is one edit.
2. Wider, high-precision detection folded into the signals that already exist.
3. A reviser that removes slop without sanding away specifics.

## Non-goals

- No change to the readability score object's shape, its nine signals, their weights,
  or the UI. Scores get harder to fool; they do not get restructured.
- No new LLM calls, agents, or models.
- No change to em-dash handling (see "Decisions", item 4).

## Architecture

New module `services/prose_style.py` — pure, no I/O, no DB, no Powabase client.

It holds the taxonomy once and exposes several **rendered views**, because consumers
need different framings of the same knowledge:

| Export | Shape | Consumer |
|---|---|---|
| `AI_WORDS` | `tuple[str, ...]` incl. inflections | `scoring.py` regex, all prompts |
| `PATTERNS` | list of `{key, name, examples, fix}` | judge taxonomy, fix instructions |
| `writer_block()` | prescriptive "avoid this" text | `generation.py`, `linkedin_gen.py` |
| `judge_taxonomy()` | detection framing | `scoring.py` `_READ_JUDGE_PROMPT` |
| `fix_instruction(key)` | surgical rewrite text | `revise.py` `_TELL_INSTRUCTION` |

The tuned regexes stay hand-written in `scoring.py` — the possessive-`its` guard at
`scoring.py:44` is load-bearing and must not be generated — but they source their word
list from `AI_WORDS` so the vocabulary cannot drift from the prompts.

`prose_style.py` imports nothing from other services, so it introduces no cycle:
`scoring.py`, `generation.py`, `revise.py`, and `linkedin_gen.py` all depend on it and
it depends on none of them.

## Taxonomy expansion

New patterns extend `_AI_WORDS` and `_TELL_RE`, feeding the **existing**
`ai_vocabulary` and `tell_phrases` signals. No signal is added.

Three consequences follow for free:

- The nine weights still sum to 1.00 — no re-weighting, no new UI row.
- `_GATE_KEYS` already contains both keys, so newly-detected slop can gate an article.
- `_LOCALIZED_TELL_KEYS` already routes both to paragraph-level surgical rewrites
  rather than whole-article regeneration.

Adding a tenth signal would have forced a re-weight of all nine and a UI change.
Folding in costs nothing.

### Added to `AI_WORDS` (~15)

utilize, facilitate, empower, streamline, cutting-edge, paradigm shift, game changer,
multifaceted, meticulous, intricate, paramount, transformative, supercharge,
ever-evolving, beacon — with inflections where they exist.

Multi-word entries (cutting-edge, paradigm shift, game changer, ever-evolving) need the
alternation's boundary assertions checked, since `_AI_WORD_RE` was built for single
tokens.

### Added to `_TELL_RE` (~10 constructions)

| Pattern | Example |
|---|---|
| Weasel attribution | "studies show", "experts agree", "widely regarded as" |
| Importance puffery | "plays a vital role", "solidifies its position" |
| Faux-insight setup | "what most people get wrong", "the part everyone misses" |
| Throat-clearing opener | "here's the thing", "let me be clear" |
| Rhetorical setup | "what if I told you", "plot twist:" |
| Superficial `-ing` analysis | ", highlighting the", ", reflecting its" |
| Negative listing | "Not a X. Not a Y." |
| Dramatic fragmentation | "That's it. That's the…" |
| Summary-recap ending | "to sum up", "in summary", "to wrap up" |
| Fake-strong verb | "serves as a" |

### The no-overlap rule

A new phrase must not contain a word already in `AI_WORDS`, or one sin scores twice —
once in `ai_vocabulary`, again in `tell_phrases`. "Stands as a testament" would hit both
`testament` and puffery.

So puffery uses "plays a vital role" and "solidifies its position" rather than the
testament phrasings, and the `-ing` pattern takes `highlighting|reflecting|showcasing`
but not `underscoring` (already banned). This rule is machine-checked by a test.

### Left to the LLM judge

Synonym cycling and colon reveals both need judgment. Colon reveals in particular would
false-positive constantly on Markdown labels and bulleted lead-ins. They belong in
`judge_taxonomy()`, not in a regex.

### Out of scope

Emoji-in-headings is formatting, not phrasing. Detecting it would distort what
`bullet_style` measures, and the writer prompt already governs formatting. It goes into
the prompt text only.

## Decisions

1. **Signal folding, not new signals.** Keeps weights, gate keys, localized-fix routing,
   and the UI untouched. (See "Taxonomy expansion".)

2. **`tell_score = 100 − hits × 15`**, down from `× 25`. The `× 25` slope was calibrated
   against 8 patterns; at ~18 patterns it would drive articles to 0 and force revision
   passes that cost credits. Note the threshold interaction: the gate fires on
   `score < 40`, so under the new slope 4 hits scores exactly 40 and does **not** gate,
   while 5 hits scores 25 and does. That is the intended sensitivity.

3. **Reviser guardrail.** Each `_TELL_INSTRUCTION` entry gains the minimum-effective-edit
   rule: rewrite only the flagged span, leave clean sentences alone, and never trade a
   concrete detail (number, name, date, mechanism) for smoother phrasing. This counters
   the homogenization an iterative score-chasing loop is prone to.

4. **Em-dash handling is unchanged.** Reviewed and deliberately left alone. Em-dashes are
   already handled in five layers: the writer prompt, the `em_dashes` signal (tolerance
   ≤3/1k), gate-key status, a nuanced critic prompt, and the `_thin_em_dashes()`
   deterministic backstop. The backstop removes every em-dash, which overshoots the
   ≤3/1k the scorer itself tolerates — but it only fires on articles already above the
   threshold, and a deterministic guarantee is worth more here than the last two dashes.
   No change.

## Testing

Backend tests only, hermetic, mocked at the existing boundaries.

**Drift-proofing (the highest-value test).** Assert that the writer block, judge
taxonomy, and fix instructions all render from the same `AI_WORDS`, so the seven copies
cannot diverge again. This tests the actual bug being fixed.

**`prose_style.py` invariants.** Every pattern has a key, name, examples, and fix. The
no-overlap rule holds: no pattern phrase contains a word already in `AI_WORDS`.

**Detection.** Each new word and construction is detected in a representative sample.

**Precision guards.** The existing false-positive protections still hold: possessive
`its` does not trip the antithesis reframe, third-person competitor attribution does not
trip `brand_voice`, and Markdown colon labels are not flagged.

**Regression.** The existing scoring suite passes unchanged — same nine signals, same
weights, same gate keys, same score-object shape.

**Calibration.** A test pins the new slope at the gate boundary: 4 hits → 40 (no gate),
5 hits → 25 (gates).

## Files touched

- `services/prose_style.py` — new
- `services/scoring.py` — `_AI_WORDS` and `_TELL_RE` source from the module; new
  constructions; `tell_score` slope; judge prompt uses `judge_taxonomy()`
- `services/generation.py` — writer block from the module
- `services/revise.py` — three copies replaced; `_TELL_INSTRUCTION` guardrail
- `services/linkedin_gen.py` — writer block from the module
- `tests/` — new `test_prose_style.py`; additions to the scoring tests

## Attribution

The pattern taxonomy derives from the `no-ai-slop` skill by Peter Yang
(<https://github.com/petergyang/no-ai-slop>), MIT licensed. `prose_style.py` carries a
module docstring crediting it.
