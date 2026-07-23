# Anti-AI-slop prose taxonomy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace seven drifted copies of RankForge's "reads as AI-written" knowledge with one shared taxonomy module, widen detection by ~15 register words and ~16 constructions, and stop the reviser from flattening prose while it removes slop.

**Architecture:** A new pure module `services/prose_style.py` owns the register words and the formulaic-construction patterns (name, examples, fix, and regex source). `scoring.py` compiles its detectors from it; `generation.py`, `linkedin_gen.py`, `revise.py`, and the readability judge render their prompt text from it. New patterns fold into the existing `ai_vocabulary` and `tell_phrases` signals, so the nine readability signals, their weights, the gate keys, and the score-object shape are all unchanged.

**Tech Stack:** Python 3.13, pytest (`asyncio_mode = "auto"` — async tests need no decorator), ruff (line-length 88). Backend only. No migration, no API change, no frontend change.

## Global Constraints

- Backend only. No DB migration, no API schema change, no frontend change.
- The readability score object keeps its exact current shape: nine signals, same keys, same weights summing to 1.00, same `_GATE_KEYS = {"em_dashes", "ai_vocabulary", "tell_phrases"}`.
- No new signal may be added. New patterns fold into `ai_vocabulary` and `tell_phrases`.
- `prose_style.py` is pure: no I/O, no DB, no Powabase client, and it imports nothing from other `services/` modules (this is what keeps it cycle-free).
- **No-overlap rule:** no construction pattern may contain a word already in the register, or one sin scores twice (once in `ai_vocabulary`, again in `tell_phrases`). Machine-checked by a test.
- Em-dash handling is out of scope and must not change: `_thin_em_dashes()`, `_TELL_INSTRUCTION["em_dashes"]`, and the `em_dashes` signal band stay exactly as they are.
- `tell_score` slope becomes `100 − hits × 15` (was `× 25`).
- Ruff line-length is 88. Run `uv run ruff check src tests` before every commit.
- Every test import goes at the top of the file (appending imports mid-file trips ruff E402).
- Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Push with `/usr/bin/git` (the snap `gh` git lacks git-remote-https).
- Do **not** run `next build` — there is no frontend work here, and it crashes this machine's WSL VM.

## File Structure

| File | Responsibility |
|---|---|
| `services/prose_style.py` (new) | The taxonomy: register words, construction patterns, and the rendered views each consumer needs |
| `services/scoring.py` (modify) | Compiles `_AI_WORD_RE` / `_TELL_RE` from the module; `tell_score` slope; judge prompt renders `judge_taxonomy()` |
| `services/generation.py` (modify) | Writer prompt renders `writer_block()` |
| `services/linkedin_gen.py` (modify) | LinkedIn prompt renders `writer_block()` |
| `services/revise.py` (modify) | Three prompt copies render from the module; `_TELL_INSTRUCTION` gains the minimum-edit guardrail |
| `tests/test_prose_style.py` (new) | Taxonomy invariants and rendered-view content |
| `tests/test_scoring.py` (modify) | Detection of the new patterns, precision guards, gate-boundary calibration |
| `tests/test_revise.py` (modify) | Fix instructions source from the module and carry the guardrail |

## Task Sequencing Rationale

Task 1 defines the taxonomy with **only the patterns that exist today**. Task 2 wires `scoring.py` to it as a **pure refactor** — `test_scoring.py` must pass untouched, which proves the plumbing before any behavior changes. Task 3 then adds the new patterns as a single reviewable behavior change. This separation means a reviewer can accept the refactor and reject the new detection, or vice versa.

---

### Task 1: Create the taxonomy module (existing patterns only)

**Files:**
- Create: `rankforge/backend/src/rankforge_backend/services/prose_style.py`
- Test: `rankforge/backend/tests/test_prose_style.py`

**Interfaces:**
- Consumes: nothing (this is the base of the dependency chain).
- Produces:
  - `Register(NamedTuple)` with fields `lemma: str`, `forms: tuple[str, ...]`, `gloss: str = ""`
  - `Pattern(NamedTuple)` with fields `key: str`, `name: str`, `examples: tuple[str, ...]`, `fix: str`, `regex: str | None = None`
  - `AI_REGISTER: tuple[Register, ...]`
  - `AI_WORDS: tuple[str, ...]` — every inflection, flattened; the regex input
  - `EMPTY_TRANSITIONS: tuple[str, ...]`
  - `PATTERNS: tuple[Pattern, ...]`
  - `register_list() -> str`
  - `tell_regex_source() -> str`
  - `tell_examples_summary(limit: int = 6) -> str`
  - `writer_block() -> str`
  - `judge_taxonomy() -> str`

- [ ] **Step 1: Write the failing test**

Create `rankforge/backend/tests/test_prose_style.py`:

```python
"""Prose taxonomy invariants (pure, hermetic)."""

import re

from rankforge_backend.services import prose_style as ps


def test_ai_words_flattens_every_register_form():
    assert ps.AI_WORDS == tuple(f for r in ps.AI_REGISTER for f in r.forms)
    # the lemma is always among its own forms, so the detector catches the base word
    for r in ps.AI_REGISTER:
        assert r.lemma in r.forms


def test_register_has_no_duplicate_forms():
    # A duplicated form would double-count in the density calculation.
    assert len(ps.AI_WORDS) == len(set(ps.AI_WORDS))


def test_every_pattern_is_complete():
    for p in ps.PATTERNS:
        assert p.key and p.name and p.fix, p
        assert p.examples, p
        assert all(e.strip() for e in p.examples), p


def test_pattern_keys_are_unique():
    keys = [p.key for p in ps.PATTERNS]
    assert len(keys) == len(set(keys))


def test_no_pattern_example_contains_a_registered_word():
    """The no-overlap rule: a construction must not embed an already-banned word, or a
    single sin scores twice — once in ai_vocabulary, again in tell_phrases."""
    banned = re.compile(
        r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in ps.AI_WORDS) + r")(?![a-z])",
        re.I,
    )
    for p in ps.PATTERNS:
        for example in p.examples:
            assert not banned.search(example), f"{p.key} example overlaps: {example}"


def test_tell_regex_source_compiles_and_excludes_judge_only_patterns():
    src = ps.tell_regex_source()
    re.compile(src, re.I)  # must not raise
    for p in ps.PATTERNS:
        if p.regex is None:
            assert p.name not in src


def test_writer_block_lists_the_register_and_every_construction():
    block = ps.writer_block()
    assert "delve" in block
    assert "leverage" in block
    for p in ps.PATTERNS:
        assert p.name in block


def test_judge_taxonomy_names_every_pattern_including_judge_only():
    tax = ps.judge_taxonomy()
    for p in ps.PATTERNS:
        assert p.name in tax
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_prose_style.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'rankforge_backend.services.prose_style'`

- [ ] **Step 3: Write the module**

Create `rankforge/backend/src/rankforge_backend/services/prose_style.py`:

```python
"""Shared prose-quality taxonomy — one source of truth for what "reads as AI-written".

The same knowledge is needed in four framings: the writer must be told what to avoid,
the readability judge what to detect, the reviser how to fix it, and the deterministic
scorer needs word lists and regex sources to compile. Those four used to live as seven
hand-maintained copies (generation.py, revise.py x3, scoring.py x2, linkedin_gen.py)
that had already drifted apart. Adding a pattern here updates the detector AND every
prompt at once.

Pure data plus string rendering: no I/O, and no imports from other services modules, so
nothing can cycle through it.

Pattern taxonomy adapted from the no-ai-slop skill by Peter Yang
(https://github.com/petergyang/no-ai-slop), MIT licensed.
"""

from typing import NamedTuple


class Register(NamedTuple):
    """One overused word plus every inflection the detector should catch.

    `forms` feeds the deterministic regex; `lemma` + `gloss` are what prompts show, so
    a prompt reads "landscape (as metaphor)" instead of dumping every conjugation.
    """

    lemma: str
    forms: tuple[str, ...]
    gloss: str = ""


class Pattern(NamedTuple):
    """One formulaic construction: how to name it, show it, fix it, and match it.

    `regex` is None for patterns that need human judgment (synonym cycling, colon
    reveals). Those are described to the LLM judge but never scored deterministically —
    a regex for them would false-positive constantly on Markdown labels and lists.
    """

    key: str
    name: str
    examples: tuple[str, ...]
    fix: str
    regex: str | None = None


AI_REGISTER: tuple[Register, ...] = (
    Register("delve", ("delve", "delved", "delving")),
    Register("tapestry", ("tapestry",)),
    Register("realm", ("realm", "realms")),
    Register("landscape", ("landscape",), "(as metaphor)"),
    Register("leverage", ("leverage", "leverages", "leveraging", "leveraged")),
    Register("robust", ("robust",)),
    Register("seamless", ("seamless", "seamlessly")),
    Register("navigate", ("navigate", "navigating"), "(as metaphor)"),
    Register("underscore", ("underscore", "underscores", "underscoring")),
    Register("foster", ("foster", "fosters", "fostering")),
    Register("harness", ("harness", "harnessing")),
    Register("elevate", ("elevate", "elevates", "elevating")),
    Register("unlock", ("unlock", "unlocks", "unlocking")),
    Register("embark", ("embark", "embarking")),
    Register("testament", ("testament",)),
    Register("pivotal", ("pivotal",)),
    Register("crucial", ("crucial",)),
    Register("vibrant", ("vibrant",)),
    Register("boasts", ("boasts", "boasting"), "(for a feature)"),
    Register("nestled", ("nestled",), "(for a place)"),
    Register("genuinely", ("genuinely",), "(as an intensifier)"),
)

AI_WORDS: tuple[str, ...] = tuple(f for r in AI_REGISTER for f in r.forms)

# Filler connectives that pad machine text. Scored as their own `transitions` signal,
# but named here so the prompts and the detector can't drift apart.
EMPTY_TRANSITIONS: tuple[str, ...] = (
    "moreover", "furthermore", "additionally", "that said",
)

PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        key="not_just",
        name='The "not just X, it\'s Y" intensifier',
        examples=("it's not just a database, it's a platform",),
        fix="State what it is and drop the negated half.",
        regex=r"it['’]s not (?:just|merely)\b|\bisn'?t (?:just|merely)\b",
    ),
    Pattern(
        key="antithesis_reframe",
        name="The antithesis reframe",
        examples=(
            "the way forward isn't more tools. It's better process",
            "it isn't about speed, it's about correctness",
        ),
        fix="Name what something IS, directly, without the negate-then-reveal setup.",
        # Bounded span so it can't run away. REQUIRE the contraction apostrophe in the
        # payoff so possessive "its" ("…isn't down, but its replacement is") is not a
        # false positive. This guard is load-bearing — do not loosen it.
        regex=r"\b(?:is|are|was|were)(?:n'?t| not)\b[^.!?\n]{0,70}[,.!?—–-]\s*it['’]s\b",
    ),
    Pattern(
        key="whether_youre",
        name='The "whether you\'re a…" inclusivity hedge',
        examples=("whether you're a beginner or a seasoned pro",),
        fix="Address the actual reader the brief names; drop the catch-all.",
        regex=r"\bwhether you'?re an?\b",
    ),
    Pattern(
        key="in_todays_world",
        name='The "in today\'s … world" opener',
        examples=("in today's fast-paced world", "in today's digital era"),
        fix="Open on the specific thing that changed, with a date or a number.",
        regex=r"\bin today'?s\b.{0,40}?\b(?:world|landscape|era|age)\b",
    ),
    Pattern(
        key="lets_dive_in",
        name='The "let\'s dive in" transition',
        examples=("let's dive in", "let's explore", "let's unpack"),
        fix="Just start the section.",
        regex=r"\blet'?s (?:dive in|explore|take a look|unpack)\b",
    ),
    Pattern(
        key="buckle_up",
        name='The "buckle up" hype opener',
        examples=("buckle up",),
        fix="Cut it and make the claim.",
        regex=r"\bbuckle up\b",
    ),
    Pattern(
        key="in_conclusion",
        name='The "in conclusion" recap',
        examples=("in conclusion",),
        fix="Close on a specific takeaway, number, or next step instead.",
        regex=r"\bin conclusion\b",
    ),
    Pattern(
        key="end_of_the_day",
        name='The "at the end of the day" filler',
        examples=("at the end of the day",),
        fix="Delete it; the sentence usually stands without it.",
        regex=r"\bat the end of the day\b",
    ),
)


def register_list() -> str:
    """The overused register as prompts should show it: lemmas with their glosses."""
    return ", ".join(f"{r.lemma} {r.gloss}".strip() for r in AI_REGISTER)


def _examples(p: Pattern) -> str:
    return "; ".join(f'"{e}"' for e in p.examples)


def tell_regex_source() -> str:
    """Alternation source for the deterministic tell detector (scoring.py compiles it
    with re.I). Judge-only patterns (regex=None) are excluded."""
    return "|".join(p.regex for p in PATTERNS if p.regex)


def tell_examples_summary(limit: int = 6) -> str:
    """A short, UI-facing list of construction shapes for the score explanation."""
    shown = [p for p in PATTERNS if p.regex][:limit]
    return ", ".join(f'"{p.examples[0]}"' for p in shown)


def writer_block() -> str:
    """Prescriptive "don't write this" block for the writer and LinkedIn prompts."""
    lines = [
        "### Overused words (worst when stacked)",
        f"- Avoid this register: {register_list()}.",
        "- Any one can be fine in isolation; never reach for several in a paragraph. "
        "Prefer plain, concrete words.",
        "",
        "### Constructions to avoid",
    ]
    lines += [f"- {p.name}: {_examples(p)}. {p.fix}" for p in PATTERNS]
    return "\n".join(lines)


def judge_taxonomy() -> str:
    """Detection framing for the readability judge — every pattern, including the
    judgment-only ones the regexes deliberately skip."""
    lines = [f"- Overused register: {register_list()}."]
    lines += [f"- {p.name}: {_examples(p)}" for p in PATTERNS]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests and ruff**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_prose_style.py -q && uv run ruff check src tests
```

Expected: `8 passed`, then `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-slop
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/prose_style.py \
  rankforge/backend/tests/test_prose_style.py
/usr/bin/git commit -m "feat(prose): add shared prose-style taxonomy module

Holds the overused register and the formulaic constructions once, with the
rendered views each consumer needs. Existing patterns only — wiring and new
patterns land in follow-ups.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire scoring.py to the module (pure refactor)

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/scoring.py:25-53`
- Test: `rankforge/backend/tests/test_scoring.py`

**Interfaces:**
- Consumes: `prose_style.AI_WORDS`, `prose_style.tell_regex_source()`.
- Produces: no new public names. `_AI_WORD_RE` and `_TELL_RE` keep their names and behavior.

**Behavior must not change.** The existing `tests/test_scoring.py` passes untouched.

- [ ] **Step 1: Write the failing test**

Append to `rankforge/backend/tests/test_scoring.py` (the `scoring` import already exists at the top of the file — do not add a second one):

```python
def test_tell_detector_still_catches_every_original_construction():
    """Refactor guard: sourcing the regex from prose_style must not drop a pattern."""
    originals = [
        "it's not just a database, it's a platform",
        "the way forward isn't more tools. It's better process",
        "whether you're a beginner or a pro",
        "in today's fast-paced world",
        "let's dive in",
        "buckle up",
        "in conclusion",
        "at the end of the day",
    ]
    for phrase in originals:
        assert scoring._TELL_RE.search(phrase), phrase


def test_possessive_its_is_not_an_antithesis_reframe():
    """The load-bearing false-positive guard: possessive "its" must not trip it."""
    assert not scoring._TELL_RE.search(
        "The service isn't down, but its replacement is still warming up."
    )


def test_ai_word_detector_still_catches_the_original_register():
    for word in ("delve", "leveraging", "seamlessly", "nestled", "genuinely"):
        assert scoring._AI_WORD_RE.search(f"we {word} here"), word
```

- [ ] **Step 2: Run test to verify it passes already**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_scoring.py -q
```

Expected: PASS. These tests characterize current behavior *before* the refactor — that is the point. They must keep passing after Step 3.

- [ ] **Step 3: Replace the literals with module-sourced compilation**

In `scoring.py`, add the import next to the other relative imports at the top of the file (alongside `from . import brief as brief_svc`):

```python
from . import prose_style
```

Then replace lines 25-53 — everything from the `# AI "tells"` comment through the closing `)` of `_TELL_RE` — with:

```python
# AI "tells" — the register/constructions search engines now penalize as machine-
# written. Detected deterministically (density matters more than any single use).
# The taxonomy itself lives in prose_style so the detector and every prompt stay in
# sync; only the compilation is here.
_AI_WORDS = prose_style.AI_WORDS
_AI_WORD_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in _AI_WORDS) + r")(?![a-z])",
    re.I,
)
_TELL_RE = re.compile(prose_style.tell_regex_source(), re.I)
```

Leave `_EMPTY_TRANSITION_RE`, `_DETACHED_VOICE_RE`, `_BOLD_BULLET_RE`, and `_BULLET_RE` exactly as they are.

- [ ] **Step 4: Run the full scoring suite unchanged**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_scoring.py -q && uv run ruff check src tests
```

Expected: all pass, including the three new characterization tests. If any fail, the refactor changed behavior — fix the module, not the test.

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-slop
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/scoring.py \
  rankforge/backend/tests/test_scoring.py
/usr/bin/git commit -m "refactor(scoring): compile AI-tell detectors from prose_style

Pure refactor — same words, same constructions, same scores. Characterization
tests pin the original patterns so the follow-up that widens the taxonomy can't
silently drop one.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Widen the taxonomy (~15 words, ~16 constructions)

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/prose_style.py` (`AI_REGISTER`, `PATTERNS`)
- Modify: `rankforge/backend/src/rankforge_backend/services/scoring.py:396-402` (the `tell_phrases` explanation string)
- Test: `rankforge/backend/tests/test_scoring.py`

**Interfaces:**
- Consumes: `Register`, `Pattern` from Task 1.
- Produces: no new names. `AI_REGISTER` and `PATTERNS` grow; every downstream view updates automatically.

- [ ] **Step 1: Write the failing test**

Append to `rankforge/backend/tests/test_scoring.py`:

```python
def test_new_register_words_are_detected():
    for word in (
        "utilize", "facilitates", "empowering", "streamlined", "multifaceted",
        "meticulously", "intricate", "paramount", "transformative", "supercharge",
        "beacon", "cutting-edge", "ever-evolving", "paradigm shift", "game changer",
    ):
        assert scoring._AI_WORD_RE.search(f"a {word} thing"), word


def test_new_constructions_are_detected():
    for phrase in (
        "studies show that teams ship faster",
        "experts agree this is the way",
        "it plays a vital role in the pipeline",
        "what most people get wrong is the eval",
        "here's the thing, nobody measures it",
        "what if I told you the index was stale",
        "the launch adds search, highlighting the team's focus",
        "Not a framework. Not a library. A runtime.",
        "That's it. That's the whole migration.",
        "to sum up, the cache was cold",
        "the gateway serves as a central hub",
    ):
        assert scoring._TELL_RE.search(phrase), phrase


def test_empty_phrases_hype_and_recap_openers_are_detected():
    for phrase in (
        "it's worth noting that the cache is cold",
        "it is important to note the tradeoff",
        "at its core, the runtime is a queue",
        "the truth is nobody measured it",
        "in this article we cover indexing",
        "when it comes to latency, p99 matters",
        "going forward we will pin the version",
        "this is huge for the team",
        "this changes everything about deploys",
    ):
        assert scoring._TELL_RE.search(phrase), phrase


def test_recap_opener_needs_line_start_and_a_comma():
    """Line-anchored so ordinary sentences survive: "Overall performance improved" is
    a fact, "Overall, ..." is a recap. Requires the detector to compile with re.M."""
    assert scoring._TELL_RE.search("Ultimately, the migration paid off.")
    assert scoring._TELL_RE.search("## Section\nOverall, we cut latency in half.")
    assert not scoring._TELL_RE.search("Overall performance improved by 12%.")
    assert not scoring._TELL_RE.search("We ultimately, and reluctantly, rolled back.")


def test_precision_guards_hold_for_the_new_constructions():
    """High-precision only: ordinary technical prose must not trip the new patterns."""
    for clean in (
        # "acts as a" was deliberately excluded — it's normal technical writing
        "The proxy acts as a load balancer for the cluster.",
        # a named, linked source is exactly what we want, not weasel attribution
        "The 2025 Stack Overflow survey reports a 12% drop.",
        # a Markdown label colon is not a colon reveal
        "- **Latency**: 40ms at p99.",
        # "summary" as a noun, not a recap opener
        "The summary field accepts 160 characters.",
        # "the source of truth is" must not trip the "the truth is" filler
        "The single source of truth is the ledger table.",
        # "in order to" / "in terms of" were deliberately left out of empty_phrase
        "We shard the table in order to keep writes under 5ms.",
    ):
        assert not scoring._TELL_RE.search(clean), clean


def test_fake_profound_kicker_is_judge_only():
    """The closing-metaphor pattern is recognizable only in context, so it must reach
    the judge but never the deterministic detector."""
    from rankforge_backend.services import prose_style as ps

    kicker = next(p for p in ps.PATTERNS if p.key == "fake_profound_kicker")
    assert kicker.regex is None
    assert kicker.name in ps.judge_taxonomy()
    assert not scoring._TELL_RE.search(
        "The best systems are the ones you never think about."
    )


def test_every_contraction_matches_both_apostrophe_forms():
    """Rendered Markdown and LLM output emit the typographic apostrophe (U+2019) far
    more than the straight one, so a straight-only branch misses most real prose."""
    straight = [
        "it isn't just a database, it's a platform",
        "whether you're a beginner or a pro",
        "in today's fast-paced world",
        "let's dive in",
        "it's worth noting the cache is cold",
        "here's the thing, nobody measures it",
        "That's it. That's the whole migration.",
    ]
    for phrase in straight:
        assert scoring._TELL_RE.search(phrase), f"straight: {phrase}"
        curly = phrase.replace("'", "’")
        assert scoring._TELL_RE.search(curly), f"typographic: {curly}"
```

Additionally, append this to **`tests/test_prose_style.py`** (not `test_scoring.py` — it
is a taxonomy invariant, and that file already imports `re` and `prose_style as ps`).
It replaces hand-listed sample phrases with an invariant that covers every branch of
every pattern automatically, so a dropped alternation can't slip through as the
taxonomy grows:

```python
def test_every_regex_pattern_matches_its_own_examples():
    """Each pattern's examples are what we tell the writer, the judge, and the reviser
    the pattern means. If an example doesn't match its own regex, the documentation and
    the detector disagree — and one of them is wrong."""
    for p in ps.PATTERNS:
        if p.regex is None:
            continue  # judge-only: described in prose, never matched deterministically
        rx = re.compile(p.regex, re.I | re.M)
        for example in p.examples:
            assert rx.search(example), f"{p.key} does not match its own example: {example}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_scoring.py -k "new_register or new_constructions" -q
```

Expected: FAIL — the new words and constructions are not in the taxonomy yet.

- [ ] **Step 3: Extend `AI_REGISTER`**

In `prose_style.py`, insert these entries at the end of the `AI_REGISTER` tuple, after the `genuinely` line and before the closing `)`:

```python
    Register("utilize", ("utilize", "utilizes", "utilized", "utilizing")),
    Register(
        "facilitate", ("facilitate", "facilitates", "facilitated", "facilitating")
    ),
    Register("empower", ("empower", "empowers", "empowered", "empowering")),
    Register(
        "streamline", ("streamline", "streamlines", "streamlined", "streamlining")
    ),
    Register("multifaceted", ("multifaceted",)),
    Register("meticulous", ("meticulous", "meticulously")),
    Register("intricate", ("intricate",)),
    Register("paramount", ("paramount",)),
    Register("transformative", ("transformative",)),
    Register(
        "supercharge",
        ("supercharge", "supercharges", "supercharged", "supercharging"),
    ),
    Register("beacon", ("beacon",)),
    Register("cutting-edge", ("cutting-edge",)),
    Register("ever-evolving", ("ever-evolving",)),
    Register("paradigm shift", ("paradigm shift", "paradigm shifts")),
    Register("game changer", ("game changer", "game-changer", "game changers")),
```

- [ ] **Step 4: Extend `PATTERNS`**

In `prose_style.py`, insert these entries at the end of the `PATTERNS` tuple, after the `end_of_the_day` entry and before the closing `)`:

```python
    Pattern(
        key="weasel_attribution",
        name="Weasel attribution",
        examples=("studies show", "experts agree", "widely regarded as"),
        fix="Name the source and link it, or cut the claim. Never invent a source.",
        # Deliberately narrow: "research shows [linked source]" is legitimate here, so
        # only the unattributable appeals are matched.
        regex=(
            r"\bstudies show\b|\bexperts agree\b|\bwidely regarded as\b"
            r"|\bmany argue\b|\bit is widely believed\b"
        ),
    ),
    Pattern(
        key="importance_puffery",
        name="Importance puffery",
        examples=("plays a vital role", "solidifies its position"),
        fix="State the fact and let the reader judge whether it matters.",
        # Avoids "pivotal" and "testament" — already in the register, and matching them
        # here would score one sin twice.
        regex=(
            r"\bplays? a (?:vital|key|critical) role\b"
            r"|\bsolidif(?:y|ies|ying) its position\b"
            r"|\bcements? its (?:position|status)\b"
            r"|\bmarks? a turning point\b"
        ),
    ),
    Pattern(
        key="faux_insight",
        name="Faux-insight setup",
        examples=("what most people get wrong", "the part everyone misses"),
        fix="Cut the setup and let the claim stand on its own.",
        regex=(
            r"\bwhat (?:most people|nobody|everyone) (?:gets? wrong|tells you|misses)\b"
            r"|\bthe part (?:most people|everyone) (?:skips?|misses)\b"
            r"|\bhere'?s what nobody\b"
        ),
    ),
    Pattern(
        key="throat_clearing",
        name="Throat-clearing opener",
        examples=("here's the thing", "let me be clear"),
        fix="Delete the opener and state the point.",
        regex=(
            r"\bhere'?s the thing\b|\blet me be clear\b"
            r"|\bhere'?s what I mean\b|\bthe uncomfortable truth\b"
        ),
    ),
    Pattern(
        key="rhetorical_setup",
        name="Rhetorical setup",
        examples=("what if I told you", "plot twist"),
        fix="Drop the setup and make the point.",
        regex=r"\bwhat if I told you\b|\bplot twist\b|\bthink about it[:.]",
    ),
    Pattern(
        key="superficial_analysis",
        name="Superficial -ing analysis",
        # The examples carry the leading comma the regex requires — an example that
        # can't match its own pattern means the docs and the detector disagree.
        examples=(
            "the launch adds search, highlighting the team's focus",
            "shipped in March, reflecting its new priorities",
        ),
        fix="Say what the fact lets someone DO, not what it supposedly signals.",
        # "underscoring" is excluded on purpose — it's already in the register.
        regex=r",\s*(?:highlighting|reflecting|showcasing)\s+(?:the|its|their|a)\b",
    ),
    Pattern(
        key="negative_listing",
        name="Negative listing",
        examples=("Not a framework. Not a library. A runtime.",),
        fix="Just say what it is.",
        regex=r"\bnot an? [^.!?\n]{1,40}\.\s*not an? \b",
    ),
    Pattern(
        key="dramatic_fragmentation",
        name="Dramatic fragmentation",
        examples=("That's it. That's the whole thing.",),
        fix="Use a complete sentence.",
        regex=r"\bthat'?s it\.\s*that'?s\b",
    ),
    Pattern(
        key="summary_recap",
        name="Summary-recap ending",
        examples=("to sum up", "in summary"),
        fix="End on the last concrete point, takeaway, or next action.",
        # Anchored to the recap phrasing so the noun "summary" ("the summary field")
        # doesn't trip it.
        regex=r"\bto sum up\b|\bin summary\b|\bto wrap (?:up|things up)\b",
    ),
    Pattern(
        key="fake_strong_verb",
        name="Fake-strong verb",
        examples=("serves as a centralized hub",),
        fix='Prefer "is" or "has", then name what it actually does.',
        # Anchored to the puffy noun: "acts as a load balancer" is ordinary technical
        # writing and must not match.
        regex=(
            r"\bserves as an? (?:[a-z-]+ ){0,2}"
            r"(?:hub|cornerstone|foundation|gateway|backbone|resource)\b"
        ),
    ),
    Pattern(
        key="empty_phrase",
        name="Empty phrase",
        examples=("it's worth noting", "when it comes to", "at its core"),
        fix="Delete the phrase and start on the point it was delaying.",
        # Only the distinctive fillers. "in order to" / "in terms of" are deliberately
        # excluded — too common in ordinary technical prose to penalize at 15 a hit.
        regex=(
            r"\bit'?s worth noting\b|\bit is worth noting\b"
            r"|\bit'?s important to note\b|\bit is important to note\b"
            r"|\bat its core\b|\bthe (?:truth|reality) is\b"
            r"|\bin this article\b|\bwhen it comes to\b|\bgoing forward\b"
        ),
    ),
    Pattern(
        key="hype_declaration",
        name="Hype declaration",
        examples=("this is huge", "this changes everything"),
        fix="Give the number or the mechanism that makes it matter, or cut the line.",
        regex=r"\bthis is huge\b|\bthis changes everything\b",
    ),
    Pattern(
        key="recap_opener",
        name="Recap opener",
        examples=("Ultimately, the migration paid off",),
        fix="End on the last concrete point instead of restating the piece.",
        # Line-anchored AND comma-anchored: "Overall performance improved" is an
        # ordinary sentence, "Overall, …" is a recap. Needs re.M at the compile site.
        regex=r"^\s*(?:ultimately|overall),",
    ),
    Pattern(
        key="fake_profound_kicker",
        name="Fake-profound kicker",
        examples=(
            "The best systems are the ones you never think about.",
            "And that, in the end, is what really counts.",
        ),
        fix=(
            "Delete the line — do NOT rewrite it into a better metaphor and do not "
            "preserve its rhythm. End on the clearest concrete sentence already in the "
            "draft, or add a plain takeaway or next action."
        ),
        regex=None,  # a closing metaphor is recognizable only in context
    ),
    Pattern(
        key="synonym_cycling",
        name="Synonym cycling",
        examples=("the agent reviews it, then the assistant scores it",),
        fix="If the clear word is right, repeat it. Don't rotate terms for style.",
        regex=None,  # needs judgment — no precise regex exists
    ),
    Pattern(
        key="colon_reveal",
        name="Colon reveal",
        examples=("The detail that makes it work: a separate agent grades it.",),
        fix="Rewrite as a plain sentence. Colons are for lists, labels, and quotes.",
        regex=None,  # would false-positive on every Markdown label and bulleted lead-in
    ),
)
```

- [ ] **Step 4b: Normalize every contraction to accept both apostrophe forms**

Task 1 established that `it['’]s` accepts the straight apostrophe (U+0027) and the
typographic one (U+2019), but every other contraction in the taxonomy still uses `'?`,
which is straight-only. Rendered Markdown and LLM output overwhelmingly emit U+2019, so
those branches silently miss most real prose. Task 2 had to be behavior-preserving and
so left them alone; here it is a deliberate behavior change.

In `prose_style.py`, replace every `'?` inside a contraction with `['’]?`:

| Pattern | Before | After |
|---|---|---|
| `not_just` | `\bisn'?t (?:just\|merely)\b` | `\bisn['’]?t (?:just\|merely)\b` |
| `antithesis_reframe` | `(?:n'?t\| not)` | `(?:n['’]?t\| not)` |
| `whether_youre` | `\bwhether you'?re an?\b` | `\bwhether you['’]?re an?\b` |
| `in_todays_world` | `\bin today'?s\b` | `\bin today['’]?s\b` |
| `lets_dive_in` | `\blet'?s (?:dive in\|…)\b` | `\blet['’]?s (?:dive in\|…)\b` |

Apply the same to the contractions in the patterns added in Step 4: `empty_phrase`
(`it'?s worth noting`, `it'?s important to note`), `throat_clearing` (`here'?s the
thing`, `here'?s what I mean`), `faux_insight` (`here'?s what nobody`), and
`dramatic_fragmentation` (`that'?s it\.\s*that'?s`).

The character after the straight `'` must be U+2019 (RIGHT SINGLE QUOTATION MARK).
Verify with:

```bash
python3 -c "
p='src/rankforge_backend/services/prose_style.py'
s=open(p,encoding='utf-8').read()
print('U+2019 count:', s.count(chr(0x2019)))
print('straight-only contractions left:', s.count(chr(0x27)+'?'))
"
```

Expected: a U+2019 count matching the number of contraction sites, and `0` remaining
straight-only `'?` occurrences.

- [ ] **Step 5: Compile the tell detector in multiline mode**

The `recap_opener` pattern anchors on `^` to mean *line* start. Python raises
`error: global flags not at the start of the expression` if a pattern embeds `(?m)`
partway through an alternation, so the flag must go at the compile site instead.

In `scoring.py`, change the `_TELL_RE` compile added in Task 2 to:

```python
# re.M so a pattern can anchor on line start (recap openers like "Ultimately,").
# Harmless to every other pattern — none of them use ^ or $.
_TELL_RE = re.compile(prose_style.tell_regex_source(), re.I | re.M)
```

- [ ] **Step 6: Generate the `tell_phrases` explanation so it can't drift**

In `scoring.py`, replace the `tell_phrases` signal's explanation string (currently the hardcoded list of six construction shapes, around lines 396-402) with:

```python
    tell_hits = len(_TELL_RE.findall(content_md))
    tell_score = max(0.0, 100.0 - tell_hits * 25)
    sig.append(_signal(
        "tell_phrases", "Formulaic constructions", tell_score, 0.14,
        f"{tell_hits} AI-tell construction(s) "
        f"({prose_style.tell_examples_summary()}, …).",
        ["Rewrite the formulaic openers/closers in a natural voice; for \"X isn't A, "
         "it's B\", just state what it is."]
        if tell_hits else []))
```

Leave the `* 25` slope alone — Task 4 changes it.

- [ ] **Step 7: Run the tests**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_prose_style.py tests/test_scoring.py -q && uv run ruff check src tests
```

Expected: all pass. The no-overlap test from Task 1 now also guards the new patterns — if it fails, a new example embeds a registered word and must be reworded.

- [ ] **Step 8: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-slop
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/prose_style.py \
  rankforge/backend/src/rankforge_backend/services/scoring.py \
  rankforge/backend/tests/test_scoring.py
/usr/bin/git commit -m "feat(prose): widen the taxonomy by ~15 words and ~16 constructions

Adds weasel attribution, importance puffery, faux-insight setups, throat-clearing
openers, rhetorical setups, superficial -ing analysis, negative listing, dramatic
fragmentation, summary-recap endings, fake-strong verbs, empty phrases, hype
declarations, and recap openers. Fake-profound kickers, synonym cycling, and colon
reveals are judge-only — no precise regex exists for any of them.

Weasel attribution matters most here: it works directly against the GEO
citability signal the product already scores.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Recalibrate the tell_phrases slope

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/scoring.py` (the `tell_score` line)
- Test: `rankforge/backend/tests/test_scoring.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new. Only the slope constant changes.

**Why:** the `× 25` slope was calibrated against 9 constructions. At ~21 it would drive articles to 0 and force revision passes that cost credits.

- [ ] **Step 1: Write the failing test**

Append to `rankforge/backend/tests/test_scoring.py`:

```python
def _md_with_tells(n: int) -> str:
    """An article body carrying exactly `n` distinct AI-tell constructions."""
    tells = [
        "In conclusion, we shipped.",
        "Let's dive in.",
        "Buckle up.",
        "At the end of the day it worked.",
        "Studies show teams ship faster.",
        "Here's the thing, nobody measures it.",
    ]
    body = " ".join("filler word here ok" for _ in range(50))
    return "# T\n\n" + body + "\n\n" + "\n\n".join(tells[:n])


def test_tell_score_slope_is_fifteen_per_hit():
    for hits, expected in ((1, 85), (2, 70), (3, 55), (4, 40), (5, 25)):
        s = scoring.score_readability(_md_with_tells(hits), None)
        tp = next(x for x in s["signals"] if x["key"] == "tell_phrases")
        assert tp["score"] == expected, (hits, tp["score"])


def test_gate_boundary_four_hits_does_not_gate_five_does():
    """The gate fires below 40, so at the new slope 4 constructions sit exactly on the
    boundary and pass, while 5 trips it. This is the intended sensitivity."""
    four = scoring.score_readability(_md_with_tells(4), {"human_voice": 95, "flow": 95})
    five = scoring.score_readability(_md_with_tells(5), {"human_voice": 95, "flow": 95})
    tp4 = next(x for x in four["signals"] if x["key"] == "tell_phrases")
    tp5 = next(x for x in five["signals"] if x["key"] == "tell_phrases")
    assert tp4["score"] == 40 and tp5["score"] == 25
    # 5 hits is below the gate floor, so the axis cannot be "met" however well it
    # scores elsewhere.
    assert five["met"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_scoring.py -k "slope or gate_boundary" -q
```

Expected: FAIL — at the `× 25` slope, 1 hit scores 75 rather than 85.

- [ ] **Step 3: Change the slope**

In `scoring.py`, change the one line:

```python
    tell_score = max(0.0, 100.0 - tell_hits * 15)
```

Add a comment above it:

```python
    # 15 per hit, not 25: the steeper slope was calibrated against 9 constructions and
    # would drive articles to 0 now the taxonomy carries ~21. The gate fires below 40,
    # so 4 hits sits on the boundary and 5 trips it.
```

- [ ] **Step 4: Run the full scoring suite**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_scoring.py -q && uv run ruff check src tests
```

Expected: all pass. If an older test asserted a specific `tell_phrases` score under the old slope, update that test's expected value — the slope change is intentional.

- [ ] **Step 5: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-slop
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/scoring.py \
  rankforge/backend/tests/test_scoring.py
/usr/bin/git commit -m "fix(scoring): recalibrate tell_phrases to 15 points per hit

The 25-point slope was tuned for 9 constructions; the taxonomy now carries ~21.
Gate boundary moves from 3 hits to 5, pinned by test.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Render the describing prompts from the module

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/generation.py:44` (`_SYSTEM_PROMPT`), lines 109-120
- Modify: `rankforge/backend/src/rankforge_backend/services/linkedin_gen.py:59` (`_SYSTEM`)
- Modify: `rankforge/backend/src/rankforge_backend/services/scoring.py:572` (`_READ_JUDGE_PROMPT`)
- Test: `rankforge/backend/tests/test_prose_style.py`

**Interfaces:**
- Consumes: `prose_style.writer_block()`, `prose_style.judge_taxonomy()`.
- Produces: no new names.

**Technique:** these prompts are plain triple-quoted strings, and other parts of them contain `{` (JSON output shapes). Do **not** convert them to f-strings — split the literal and concatenate, so no brace needs escaping.

- [ ] **Step 1: Write the failing test**

Append to `rankforge/backend/tests/test_prose_style.py`:

```python
def test_every_prompt_renders_the_shared_register():
    """Drift-proofing: this is the actual bug being fixed. The writer, the LinkedIn
    generator, and the readability judge must all show the SAME register, so the
    seven hand-maintained copies can never diverge again."""
    from rankforge_backend.services import generation, linkedin_gen, scoring

    sample = ps.AI_REGISTER[0].lemma  # "delve"
    newest = ps.AI_REGISTER[-1].lemma  # a word added in the widening task
    for prompt in (
        generation._SYSTEM_PROMPT,
        linkedin_gen._SYSTEM,
        scoring._READ_JUDGE_PROMPT,
    ):
        assert sample in prompt
        assert newest in prompt, "a prompt is not rendering the shared register"


def test_prompts_name_the_new_constructions():
    from rankforge_backend.services import generation, scoring

    for name in ("Weasel attribution", "Faux-insight setup"):
        assert name in generation._SYSTEM_PROMPT
        assert name in scoring._READ_JUDGE_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_prose_style.py -k "prompt" -q
```

Expected: FAIL — the prompts still carry their own hardcoded lists, which lack the newly added words.

- [ ] **Step 3: Rewire `generation.py`**

Add `from . import prose_style` to the relative imports at the top.

The prompt currently runs `_SYSTEM_PROMPT = """\` … with a `## Write like a human, not an AI` section. Split it: cut lines 109-120 (the `### Overused words (worst when stacked)` heading through the `- "From X to Y" framing…` line) out of the literal, close the string there, and concatenate.

The result should read:

```python
_SYSTEM_PROMPT = """\
...everything up to and including the line:
Editors reject copy that reads as machine-written. Steer clear of all of these:

""" + prose_style.writer_block() + """

### Punctuation and rhythm
- Use em-dashes rarely; prefer commas, periods, or parentheses.
...the rest of the prompt, unchanged...
"""
```

Two RankForge-specific constructions are **not** in the shared taxonomy and must be kept in the local text (append them under the concatenated block, in the `### Punctuation and rhythm` section's place or just above it):

```
- Reflexive rule-of-three triads ("fast, reliable, and scalable"); vary list length and rhythm instead.
- "From X to Y" framing ("from startups to enterprises").
```

- [ ] **Step 4: Rewire `linkedin_gen.py`**

Add `from . import prose_style` to the relative imports.

Replace the `NEVER USE these AI-tell words:` paragraph and the `NEVER USE these AI-tell constructions:` list (lines ~92-105) with a concatenation, keeping the surrounding LinkedIn-specific guidance intact:

```python
_SYSTEM = """You write LinkedIn posts for a brand, repurposing one of its own blog \
...everything up to the VOICE section, unchanged...

""" + prose_style.writer_block() + """

Generic, specificity-free prose is the clearest AI tell — be concrete.

OUTPUT: only the post text itself, ready to paste — no preamble, no quotes, no \
"Here's your post". Fixed trailing order: hook + body, then the discussion question as \
the last body line, then (only if a link is provided in the instructions) a blank line \
and a soft "Full write-up → {url}" line, then a blank line and 3-5 specific, relevant \
hashtags (never generic spam)."""
```

Note the literal `{url}` near the end — this is exactly why the prompt must stay a plain string rather than become an f-string.

- [ ] **Step 5: Rewire the readability judge**

In `scoring.py`, the `_READ_JUDGE_PROMPT` currently inlines the register and constructions inside the `human_voice` axis description. Replace that axis bullet with a concatenation:

```python
_READ_JUDGE_PROMPT = """\
Rate the article on two axes (0–100 each) for how HUMAN it reads. High means a \
knowledgeable person clearly wrote it; low means it reads as machine-generated.

## Axes
- human_voice — a real point of view, confident unqualified claims, and concrete \
specificity (numbers, names, dates, examples). Penalize every pattern below:

""" + prose_style.judge_taxonomy() + """

- flow — natural rhythm (a mix of short and long sentences, uneven section lengths) \
that reads smoothly, NOT the mechanical evenness and over-even paragraphing of \
machine text. Penalize excessive em-dashes and filler transitions (moreover, \
furthermore, additionally, that said).

## For each axis return
- The score (0–100).
- A one-line note explaining the score.
- A short list of concrete fixes (empty when none are needed).

## Output
Return ONLY this JSON object:
{"human_voice": int, "human_voice_note": str, "human_voice_fixes": [str], \
"flow": int, "flow_note": str, "flow_fixes": [str]}\
"""
```

The trailing JSON shape contains braces — another reason this stays a plain concatenated string.

- [ ] **Step 6: Run the full backend suite**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest -q && uv run ruff check src tests
```

Expected: all tests pass (the full suite, not just the touched files — prompt edits can break `test_generation.py` or `test_linkedin.py` if a marker string they assert on moved).

- [ ] **Step 7: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-slop
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/generation.py \
  rankforge/backend/src/rankforge_backend/services/linkedin_gen.py \
  rankforge/backend/src/rankforge_backend/services/scoring.py \
  rankforge/backend/tests/test_prose_style.py
/usr/bin/git commit -m "refactor(prompts): render writer, LinkedIn, and judge from prose_style

Four of the seven copies are gone. A drift test asserts all three prompts show the
same register, so they can't diverge again.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Reviser — module-sourced fixes plus the minimum-edit guardrail

**Files:**
- Modify: `rankforge/backend/src/rankforge_backend/services/prose_style.py` (add `MINIMUM_EDIT_RULE`, `fix_instruction()`)
- Modify: `rankforge/backend/src/rankforge_backend/services/revise.py:92-99` (reviser's de-AI section), `:510-516` (critic prompt), `:899-918` (`_TELL_INSTRUCTION`)
- Test: `rankforge/backend/tests/test_revise.py`

**Interfaces:**
- Consumes: `prose_style.writer_block()`, `prose_style.register_list()`.
- Produces:
  - `prose_style.MINIMUM_EDIT_RULE: str`
  - `prose_style.fix_instruction(signal_key: str) -> str` — accepts `"ai_vocabulary"`, `"tell_phrases"`, `"transitions"`; raises `KeyError` otherwise.

- [ ] **Step 1: Write the failing test**

Append to `rankforge/backend/tests/test_revise.py` (put the import with the others at the top of the file — a mid-file import trips ruff E402):

```python
def test_fix_instructions_carry_the_minimum_edit_guardrail():
    from rankforge_backend.services import prose_style as ps

    for key in ("ai_vocabulary", "tell_phrases", "transitions"):
        assert ps.MINIMUM_EDIT_RULE in ps.fix_instruction(key), key


def test_fix_instruction_rejects_signals_it_does_not_own():
    """brand_voice and em_dashes are hand-written in revise.py — they aren't part of
    the shared taxonomy, and silently returning generic text for them would be worse
    than failing loudly."""
    import pytest as _pytest

    from rankforge_backend.services import prose_style as ps

    for key in ("brand_voice", "em_dashes"):
        with _pytest.raises(KeyError):
            ps.fix_instruction(key)


def test_localized_tell_instructions_source_from_the_taxonomy():
    from rankforge_backend.services import prose_style as ps
    from rankforge_backend.services import revise

    newest = ps.AI_REGISTER[-1].lemma
    assert newest in revise._TELL_INSTRUCTION["ai_vocabulary"]
    # every signal the reviser can surgically fix still has an instruction
    for key in revise._LOCALIZED_TELL_KEYS:
        assert revise._TELL_INSTRUCTION[key].strip()


def test_em_dash_instruction_is_unchanged():
    """Em-dash handling is deliberately out of scope: the zero-tolerance backstop and
    its instruction must survive this refactor untouched."""
    from rankforge_backend.services import revise

    assert "Do not leave a single em-dash" in revise._TELL_INSTRUCTION["em_dashes"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest tests/test_revise.py -k "minimum_edit or fix_instruction or localized_tell or em_dash_instruction" -q
```

Expected: FAIL — `AttributeError: module ... has no attribute 'MINIMUM_EDIT_RULE'`

- [ ] **Step 3: Add the guardrail and the fix renderer to `prose_style.py`**

Append to `prose_style.py`:

```python
# The reviser runs in a loop that chases a score, and loops like that sand prose smooth:
# each pass has a local reason to rewrite one more sentence. This rule is what keeps a
# slop fix from becoming a rewrite — it rides on every instruction the reviser gets.
MINIMUM_EDIT_RULE = (
    "Rewrite ONLY the flagged span. Leave clean sentences alone, and never trade a "
    "concrete detail (a number, name, date, version, or mechanism) for smoother "
    "phrasing — losing a specific is worse than the tell you removed."
)


def fix_instruction(signal_key: str) -> str:
    """The surgical rewrite instruction for a readability signal this module owns.

    Raises KeyError for `brand_voice` and `em_dashes`: those aren't part of the prose
    taxonomy (one is brand-specific, the other is punctuation policy), so they stay
    hand-written in revise.py. Failing loudly beats returning plausible generic text.
    """
    if signal_key == "ai_vocabulary":
        body = (
            f"Replace AI-register words ({register_list()}) with plain, specific "
            "language."
        )
    elif signal_key == "tell_phrases":
        shapes = "; ".join(f'"{p.examples[0]}"' for p in PATTERNS if p.regex)
        body = f"Rewrite formulaic AI constructions in a natural voice: {shapes}."
    elif signal_key == "transitions":
        listed = ", ".join(w.capitalize() for w in EMPTY_TRANSITIONS)
        body = (
            f"Cut filler transitions ({listed}); let the sentences connect directly."
        )
    else:
        raise KeyError(signal_key)
    return f"{body} {MINIMUM_EDIT_RULE}"
```

- [ ] **Step 4: Rewire `_TELL_INSTRUCTION` in `revise.py`**

Add `from . import prose_style` to the relative imports at the top.

Replace the `_TELL_INSTRUCTION` dict (lines ~899-918) with:

```python
_EM_DASH_RE = re.compile(r"—")
_TELL_INSTRUCTION = {
    # brand_voice and em_dashes are NOT in the shared taxonomy: one is brand-specific,
    # the other is punctuation policy with a deterministic backstop. They stay here.
    "brand_voice": "Rewrite detached self-reference into the brand's FIRST-PERSON "
                   'champion voice: "the vendor asserts…"/"the platform documents…"/'
                   '"according to <brand>\'s own docs…" become the brand naming itself '
                   'or "we"/"our", stating its own capabilities as fact (e.g. '
                   '"Powabase\'s runtime has hard safeguards…", "Our documentation '
                   'details the pitfalls…"). Keep third-person only for competitors.',
    "em_dashes": "Remove every em-dash (—); use a comma, period, or parentheses "
                 "instead. Do not leave a single em-dash.",
    "tell_phrases": prose_style.fix_instruction("tell_phrases"),
    "ai_vocabulary": prose_style.fix_instruction("ai_vocabulary"),
    "transitions": prose_style.fix_instruction("transitions"),
}
```

- [ ] **Step 5: Rewire the reviser's two remaining prompt copies**

In `revise.py`'s `_SYSTEM` (the de-AI section, lines ~92-99), replace the `### Overused words` and `### Constructions to delete` subsections with a concatenation, keeping the RankForge-specific rhythm/structure/tone bullets that follow:

```python
_SYSTEM = """\
...everything up to and including:
A draft that reads as AI-written is not "improved". As you revise, actively rewrite out every one of these:

""" + prose_style.writer_block() + """

### Rhythm and punctuation
- Thin out em-dashes (prefer commas, periods, parentheses).
...the rest, unchanged...
"""
```

In `_EDITOR_SYSTEM` (the critic prompt, lines ~510-516), replace the `- Formulaic constructions: …` and `- Overused register: …` bullets with:

```python
""" + prose_style.judge_taxonomy() + """
```

keeping the surrounding bullets (mechanical evenness, generic phrasing, empty transitions, detached self-reference, and the em-dash paragraph) exactly as they are.

- [ ] **Step 6: Run the full backend suite**

```bash
cd /home/zipeng/worktrees/rankforge-slop/rankforge/backend
uv run pytest -q && uv run ruff check src tests
```

Expected: all pass. `test_revise.py` is the one most likely to break — it asserts on prompt marker strings.

- [ ] **Step 7: Commit**

```bash
cd /home/zipeng/worktrees/rankforge-slop
/usr/bin/git add rankforge/backend/src/rankforge_backend/services/prose_style.py \
  rankforge/backend/src/rankforge_backend/services/revise.py \
  rankforge/backend/tests/test_revise.py
/usr/bin/git commit -m "feat(revise): source fix instructions from prose_style, add minimum-edit rule

The last three copies are gone. Every instruction the reviser gets now carries the
minimum-effective-edit rule, so clearing a tell can't quietly cost a number, name,
date, or mechanism. Em-dash handling is untouched by design.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] Full backend suite green: `cd rankforge/backend && uv run pytest -q`
- [ ] Lint clean: `uv run ruff check src tests`
- [ ] Zero remaining copies: `grep -rn "delve" rankforge/backend/src/` returns **only** `prose_style.py`
- [ ] Score shape intact: `uv run pytest tests/test_scoring.py -k "weights_sum_to_one" -q` passes for both SEO and readability
- [ ] Em-dash scope respected: `git diff origin/main -- rankforge/backend/src/rankforge_backend/services/revise.py | grep -i "thin_em_dashes"` returns nothing

Do **not** run `next build` — there is no frontend change in this plan, and it crashes this machine's WSL VM.
