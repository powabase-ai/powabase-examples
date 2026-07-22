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
        regex=(
            r"\b(?:is|are|was|were)(?:n'?t| not)\b[^.!?\n]{0,70}"
            r"[,.!?—–-]\s*it['’]s\b"
        ),
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
