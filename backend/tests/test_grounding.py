"""Grounding retrieval post-filter (pure)."""

from rankforge_backend.services import grounding


def test_filter_drops_weak_tail():
    # floor = max(0.5, 0.98 * 0.75 = 0.735) → keeps >= 0.735
    res = [
        {"score": 0.98, "text": "a"},
        {"score": 0.95, "text": "b"},
        {"score": 0.70, "text": "c"},
        {"score": 0.68, "text": "d"},
    ]
    kept = grounding._filter_by_score(res)
    assert [r["text"] for r in kept] == ["a", "b"]


def test_filter_keeps_all_when_uniformly_high():
    res = [{"score": 0.96}, {"score": 0.95}, {"score": 0.94}]
    assert len(grounding._filter_by_score(res)) == 3


def test_filter_absolute_floor_keeps_top_on_poor_match():
    # All below the absolute floor → keep only the single best (don't ground on junk).
    res = [{"score": 0.45, "text": "a"}, {"score": 0.30, "text": "b"}]
    kept = grounding._filter_by_score(res)
    assert [r["text"] for r in kept] == ["a"]


def test_filter_passthrough_when_no_scores():
    res = [{"text": "a"}, {"text": "b"}]
    assert grounding._filter_by_score(res) == res


def test_filter_empty():
    assert grounding._filter_by_score([]) == []
