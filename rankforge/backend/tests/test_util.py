"""Shared text helpers."""

from rankforge_backend.util import strip_em_dashes


def test_strip_em_dashes_replaces_with_comma():
    assert strip_em_dashes("fast — and cheap") == "fast, and cheap"
    assert strip_em_dashes("word—word") == "word, word"
    assert strip_em_dashes("a —b") == "a, b"


def test_strip_em_dashes_collapses_doubled_commas():
    assert strip_em_dashes("a, — b") == "a, b"


def test_strip_em_dashes_leaves_endash_and_hyphen():
    # en-dash ranges and hyphens must survive
    assert strip_em_dashes("2025–2026, well-known") == "2025–2026, well-known"


def test_strip_em_dashes_skips_code():
    md = "```\ncode — stays\n```\nprose — changes"
    out = strip_em_dashes(md)
    assert "code — stays" in out  # fenced block untouched
    assert "prose, changes" in out
    # inline code span untouched, surrounding prose normalized
    assert strip_em_dashes("use `a — b` inline — here") == "use `a — b` inline, here"


def test_strip_em_dashes_noop_without_emdash():
    assert strip_em_dashes("plain text") == "plain text"
