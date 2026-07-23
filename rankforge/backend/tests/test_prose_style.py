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
        # The rule applies just as much to the regex SOURCE: if a pattern's own regex
        # text embeds a registered word literally, real prose that matches the pattern
        # will score in both ai_vocabulary and tell_phrases for the same sin.
        if p.regex is None:
            continue
        assert not banned.search(p.regex), f"{p.key} regex source overlaps a registered word"


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


def test_tell_patterns_match_typographic_apostrophes():
    """Real prose (and rendered Markdown) uses ’ far more than '. A detector that only
    handles the straight apostrophe silently misses most real input."""
    detector = re.compile(ps.tell_regex_source(), re.I)
    assert detector.search("it’s not just a database, it’s a platform")
    assert detector.search("the way forward isn’t more tools. It’s better process")


def test_tell_examples_summary_returns_examples_and_respects_limit():
    summary = ps.tell_examples_summary()
    assert summary
    first_pattern_with_regex = next(p for p in ps.PATTERNS if p.regex)
    assert first_pattern_with_regex.examples[0] in summary

    limited = ps.tell_examples_summary(limit=1)
    assert limited == f'"{first_pattern_with_regex.examples[0]}"'


def test_matched_pattern_examples_names_only_what_actually_fired():
    """The score explanation must name real offenders, not a fixed declaration-order
    sample — so it should report the pattern that occurs in the text and must NOT
    report one that doesn't."""
    text = "Honestly, let's dive in and see what breaks."
    result = ps.matched_pattern_examples(text)
    assert "let's dive in" in result  # lets_dive_in genuinely fired
    assert "buckle up" not in result  # buckle_up did not fire


def test_matched_pattern_examples_empty_when_nothing_fires():
    clean = "The API returns 200 in about 40ms across 1,000 requests."
    assert ps.matched_pattern_examples(clean) == ""


def test_matched_pattern_examples_respects_limit_and_marks_more():
    # Craft text hitting more constructions than the default limit so the ellipsis
    # marker proves the cap is real, not just a coincidence of pattern count.
    text = (
        "Let's dive in. Buckle up. In conclusion, it worked. "
        "At the end of the day it shipped. Studies show it works."
    )
    limited = ps.matched_pattern_examples(text, limit=2)
    assert limited.endswith(", …")
    assert limited.count('"') == 4  # exactly two quoted examples shown


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
    from rankforge_backend.services import generation, linkedin_gen, scoring

    for name in ("Weasel attribution", "Faux-insight setup"):
        assert name in generation._SYSTEM_PROMPT
        assert name in linkedin_gen._SYSTEM
        assert name in scoring._READ_JUDGE_PROMPT


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


def _split_top_level_alternation(pattern: str) -> list[str]:
    """Split a regex source on `|` at depth 0 only — i.e. NOT inside a `(...)` group
    and NOT inside a `[...]` character class. A naive `str.split("|")` breaks on both:
    `(?:a|b)` would wrongly split into two "branches" that are really one alternative,
    and a literal `|` inside a class (none of ours has one, but the splitter must not
    assume that) would wrongly split mid-class."""
    branches: list[str] = []
    depth = 0
    in_class = False
    escape = False
    start = 0
    for i, ch in enumerate(pattern):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_class:
            if ch == "]":
                in_class = False
            continue
        if ch == "[":
            in_class = True
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth -= 1
            continue
        if ch == "|" and depth == 0:
            branches.append(pattern[start:i])
            start = i + 1
    branches.append(pattern[start:])
    return branches


def test_split_top_level_alternation_respects_groups_and_classes():
    """Sanity-check the splitter itself before trusting it for the branch audit."""
    assert _split_top_level_alternation(r"a(?:b|c)|d") == [r"a(?:b|c)", "d"]
    assert _split_top_level_alternation(r"[a|b]|c") == [r"[a|b]", "c"]
    assert _split_top_level_alternation(r"(?:a|(?:b|c))|d") == [r"(?:a|(?:b|c))", "d"]
    assert _split_top_level_alternation("no-pipe-here") == ["no-pipe-here"]


# One positive-match string per TOP-LEVEL alternation branch of every regex-bearing
# pattern (nested alternatives inside a `(?:...)` group are exercised together, since
# they belong to the same top-level branch and matching any one of them satisfies it).
# This is what turns the taxonomy from a snapshot into an invariant: a future edit
# that adds an uncovered branch, or narrows an existing one out from under its corpus
# entry, fails loudly here instead of shipping a silently-dead branch.
_BRANCH_CORPUS: dict[str, list[str]] = {
    "not_just": [
        "it's not just a database, it's a platform",
        "the tool isn't merely a linter",
    ],
    "antithesis_reframe": [
        "the way forward isn't more tools. It's better process",
    ],
    "whether_youre": ["whether you're a beginner or a seasoned pro"],
    "in_todays_world": ["in today's fast-paced world"],
    "lets_dive_in": ["let's dive in"],
    "buckle_up": ["buckle up"],
    "in_conclusion": ["in conclusion"],
    "end_of_the_day": ["at the end of the day"],
    "weasel_attribution": [
        "studies show that teams ship faster",
        "experts agree this is the way",
        "it is widely regarded as the best tool",
        "many argue this is unnecessary",
        "it is widely believed that caching helps",
    ],
    "importance_puffery": [
        "it plays a vital role in the pipeline",
        "solidifies its position as the leader",
        "cements its position as the leader",
        "marks a turning point for the team",
    ],
    "faux_insight": [
        "what most people get wrong is the eval",
        "the part everyone misses is the index",
        "here's what nobody tells you about caching",
    ],
    "throat_clearing": [
        "here's the thing, nobody measures it",
        "let me be clear about the tradeoffs",
        "here's what I mean by fast",
        "the uncomfortable truth is nobody tested it",
    ],
    "rhetorical_setup": [
        "what if I told you the index was stale",
        "plot twist: it was cache invalidation",
        "think about it: the cache never expired",
    ],
    "superficial_analysis": [
        "the launch adds search, highlighting the team's focus",
    ],
    "negative_listing": ["Not a framework. Not a library. A runtime."],
    "dramatic_fragmentation": ["That's it. That's the whole thing."],
    "summary_recap": [
        "to sum up, the cache was cold",
        "in summary, the tradeoffs are clear",
        "to wrap up, the key results are clear",
    ],
    "fake_strong_verb": ["serves as a centralized hub"],
    "empty_phrase": [
        "it's worth noting the cache is cold",
        "it is worth noting the tradeoff",
        "it's important to note the risk",
        "it is important to note the tradeoff",
        "at its core, the runtime is a queue",
        "the truth is nobody measured it",
    ],
    "hype_declaration": [
        "this is huge for the team",
        "this changes everything about deploys",
    ],
    "recap_opener": ["Ultimately, the migration paid off"],
}


def test_every_top_level_branch_has_a_covering_positive_case():
    """Branch-level invariant: every top-level alternation branch of every
    regex-bearing pattern must be matched by at least one string in its corpus
    entry. Missing a `_BRANCH_CORPUS[key]` entry, or a branch none of its strings
    reach, fails with the exact uncovered branch text."""
    for p in ps.PATTERNS:
        if p.regex is None:
            continue
        corpus = _BRANCH_CORPUS.get(p.key)
        assert corpus, f"{p.key}: no branch corpus entry"
        for branch in _split_top_level_alternation(p.regex):
            rx = re.compile(branch, re.I | re.M)
            assert any(rx.search(s) for s in corpus), (
                f"{p.key}: no corpus string covers branch {branch!r}"
            )


def test_register_sample_returns_slash_joined_lemmas():
    """register_sample() renders SAMPLE_LEMMAS, slash-joined — a fixed, curated
    subset rather than whatever happens to be first in AI_REGISTER declaration
    order (which included the least-representative words "tapestry"/"realm")."""
    sample = ps.register_sample()
    assert "/" in sample  # should be slash-joined
    assert sample == "delve/leverage/robust/seamless/elevate"  # SAMPLE_LEMMAS


def test_register_sample_respects_limit():
    """register_sample() honors the limit parameter."""
    sample_2 = ps.register_sample(limit=2)
    assert sample_2 == "delve/leverage"
    assert sample_2.count("/") == 1  # exactly one slash for two lemmas

    sample_1 = ps.register_sample(limit=1)
    assert sample_1 == "delve"
    assert "/" not in sample_1  # no slash for one lemma


def test_sample_lemmas_are_real_register_lemmas():
    """Every SAMPLE_LEMMAS entry must be an actual AI_REGISTER lemma, not just an
    AI_WORDS inflection — register_sample() renders lemmas, not surface forms."""
    lemmas = {r.lemma for r in ps.AI_REGISTER}
    for lemma in ps.SAMPLE_LEMMAS:
        assert lemma in lemmas, lemma


def test_register_sample_lemmas_are_in_ai_words():
    """Every lemma in register_sample() is actually in ps.AI_WORDS."""
    sample = ps.register_sample()
    lemmas = sample.split("/")
    for lemma in lemmas:
        assert lemma in ps.AI_WORDS, f"{lemma} from register_sample() not in AI_WORDS"
