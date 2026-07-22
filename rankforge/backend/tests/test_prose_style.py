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
