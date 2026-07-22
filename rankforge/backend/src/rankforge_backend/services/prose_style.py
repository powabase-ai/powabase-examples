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
    Register("cutting-edge", ("cutting-edge",)),
    Register("ever-evolving", ("ever-evolving",)),
    Register("paradigm shift", ("paradigm shift", "paradigm shifts")),
    Register("game changer", ("game changer", "game-changer", "game changers")),
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
        regex=r"it['’]s not (?:just|merely)\b|\bisn['’]?t (?:just|merely)\b",
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
            r"\b(?:is|are|was|were)(?:n['’]?t| not)\b[^.!?\n]{0,70}"
            r"[,.!?—–-]\s*it['’]s\b"
        ),
    ),
    Pattern(
        key="whether_youre",
        name='The "whether you\'re a…" inclusivity hedge',
        examples=("whether you're a beginner or a seasoned pro",),
        fix="Address the actual reader the brief names; drop the catch-all.",
        regex=r"\bwhether you['’]?re an?\b",
    ),
    Pattern(
        key="in_todays_world",
        name='The "in today\'s … world" opener',
        examples=("in today's fast-paced world", "in today's digital era"),
        fix="Open on the specific thing that changed, with a date or a number.",
        regex=r"\bin today['’]?s\b.{0,40}?\b(?:world|era|age)\b",
    ),
    Pattern(
        key="lets_dive_in",
        name='The "let\'s dive in" transition',
        examples=("let's dive in", "let's explore", "let's unpack"),
        fix="Just start the section.",
        regex=r"\blet['’]?s (?:dive in|explore|take a look|unpack)\b",
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
            r"\b(?:plays?|played) a (?:vital|key|critical) role\b"
            r"|\bsolidif(?:y|ies|ied|ying) its position\b"
            r"|\b(?:cements?|cemented) its (?:position|status)\b"
            r"|\b(?:marks?|marked) a turning point\b"
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
            r"|\bhere['’]?s what nobody\b"
        ),
    ),
    Pattern(
        key="throat_clearing",
        name="Throat-clearing opener",
        examples=("here's the thing", "let me be clear"),
        fix="Delete the opener and state the point.",
        regex=(
            r"\bhere['’]?s the thing\b|\blet me be clear\b"
            r"|\bhere['’]?s what I mean\b|\bthe uncomfortable truth\b"
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
        # Requires an ABSTRACT signal noun as the object, so concrete/number-carrying
        # clauses ("reflecting the four content types", "reflecting a Google core
        # update") do not match.
        regex=(
            r",\s*(?:highlighting|reflecting|showcasing)\s+(?:the|its|their|a)\s+"
            r"(?:[\w'’-]+\s+){0,2}"
            r"(?:commitment|dedication|focus|importance|significance|expertise"
            r"|priorities|values?|mission|vision|ambition)\b"
        ),
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
        regex=r"\bthat['’]?s it\.\s*that['’]?s\b",
    ),
    Pattern(
        key="summary_recap",
        name="Summary-recap ending",
        examples=("to sum up, the cache was cold", "in summary, the tradeoffs are clear"),
        fix="End on the last concrete point, takeaway, or next action.",
        # Requires the recap punctuation (a comma or sentence end right after "in
        # summary") so the noun "summary" ("in summary view", "in summary tables")
        # doesn't trip it.
        regex=r"\bto sum up\b|\bin summary(?:[,.!?]|$)|\bto wrap (?:up|things up)\b",
    ),
    Pattern(
        key="fake_strong_verb",
        name="Fake-strong verb",
        examples=("serves as a centralized hub",),
        fix='Prefer "is" or "has", then name what it actually does.',
        # Anchored to a small set of reliably-puffy nouns only. "gateway", "backbone",
        # "foundation", and "resource" are ordinary infra nouns ("serves as a gateway
        # to the internal network") and must not match; "acts as a load balancer" is
        # likewise excluded by not matching "acts as".
        regex=(
            r"\bserves as an? (?:[a-z-]+ ){0,2}"
            r"(?:hub|cornerstone)\b"
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
            r"\bit['’]?s worth noting\b|\bit is worth noting\b"
            r"|\bit['’]?s important to note\b|\bit is important to note\b"
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
