"""Blog-profile linking rules: gap de-duplication, caps, ordering, min-gap arithmetic
and hub targets (PR #26 review round 1)."""

from typing import Any

from rankforge_backend.models.blog import BlogProfile
from rankforge_backend.services import linking as lk

BID = "11111111-1111-1111-1111-111111111111"
AID = "55555555-5555-5555-5555-555555555555"
T1 = "aaaaaaaa-0000-0000-0000-000000000001"
T2 = "aaaaaaaa-0000-0000-0000-000000000002"
T3 = "aaaaaaaa-0000-0000-0000-000000000003"
T4 = "aaaaaaaa-0000-0000-0000-000000000004"
T5 = "aaaaaaaa-0000-0000-0000-000000000005"
PILLAR = "bbbbbbbb-0000-0000-0000-000000000001"
VDB = "https://powabase.ai/vector-database/"
MVP = "https://powabase.ai/free-mvp/"


def _brand(**links: Any) -> dict[str, Any]:
    prof = BlogProfile.model_validate({
        "categories": [
            {"key": "rag", "label": "R", "technical": True},
            {"key": "agents", "label": "A", "technical": True},
            {"key": "enterprise", "label": "E", "technical": False},
        ],
        "links": {"min": 3, "max": 5, "hub_pages": [
            {"path": "/vector-database/", "title": "Vector DB",
             "topics": ["vector database", "pgvector"]},
            {"path": "/free-mvp/", "title": "Free MVP", "topics": ["free mvp"]},
        ], **links},
    }).model_dump()
    return {"id": BID, "domain": "powabase.ai",
            "url_pattern": "https://powabase.ai/blog/{slug}", "blog_profile": prof}


def _row(tid: str, title: str, kw: list[str], cat: str = "rag") -> dict[str, Any]:
    return {"id": tid, "title": title, "slug": title.lower().replace(" ", "-"),
            "keywords": kw, "canonical_url": None, "category": cat}


class FakeDB:
    """Just enough of Database for suggest_links: published rows, cluster pillar
    lookups, and a link_suggestions table that keeps its pending rows and CONFLICTS
    (returns None, like ON CONFLICT DO NOTHING) on a duplicate of the unique keys
    (target article or hub URL, lower(coalesce(anchor, '')))."""

    def __init__(self, rows: list[dict[str, Any]], pillar: dict | None = None):
        self.rows = rows
        self.pillar = pillar
        self.inserts: list[tuple] = []  # every insert attempted
        self.pending: list[dict[str, Any]] = []  # rows actually stored

    @staticmethod
    def _key(r: dict[str, Any]) -> tuple:
        tid = r["target_article_id"]
        return (str(tid) if tid else None, None if tid else r["target_url"],
                (r["anchor_text"] or "").lower())

    def fetch_all(self, q: str, p: Any = None) -> list[dict[str, Any]]:
        if "from public.link_suggestions" in q:
            return [dict(r) for r in self.pending]
        if "cluster_role = 'member'" in q:
            return []
        return list(self.rows)

    def fetch_one(self, q: str, p: Any = None) -> dict[str, Any] | None:
        if "insert into public.link_suggestions" in q:
            self.inserts.append(p)
            row = {"target_article_id": p[2], "anchor_text": p[3],
                   "target_url": p[4], "kind": p[7]}
            if any(self._key(r) == self._key(row) for r in self.pending):
                return None  # on conflict do nothing
            self.pending.append(row)
            return dict(row)
        if "content_clusters" in q:
            return {"pillar_article_id": self.pillar["id"]} if self.pillar else None
        if "status = 'published'" in q:
            if self.pillar and str(p[0]) == self.pillar["id"]:
                return self.pillar
            return next((r for r in self.rows if str(r["id"]) == str(p[0])), None)
        return None

    def execute(self, q: str, p: Any = None) -> None:
        pass


def _run(monkeypatch, body: str, rows, brand=None, *, cluster=None, pillar=None,
         brief=None, db=None):
    brand = brand or _brand()
    art = {"id": AID, "business_id": BID, "content_md": body,
           "cluster_id": cluster, "cluster_role": "member" if cluster else None,
           "brief_id": "br" if brief else None}
    monkeypatch.setattr(lk.gen_svc, "get_article", lambda d, aid: art)
    monkeypatch.setattr(lk.gen_svc, "get_brief", lambda d, bid: brief)
    monkeypatch.setattr(lk.brands, "get_profile", lambda d, bid: brand)
    db = db or FakeDB(rows, pillar)
    out = lk.suggest_links(db, BID, AID)
    return db, out


def _to(db: FakeDB, tid: str) -> list[tuple]:
    return [p for p in db.inserts if str(p[2]) == tid]


# --- I1: an anchored suggestion to X means no gap to X ---
def test_no_gap_to_a_target_that_already_has_an_anchored_suggestion(monkeypatch):
    rows = [_row(T1, "Tech One", ["tech one guide"]), _row(T2, "Tech Two", [])]
    db, _ = _run(monkeypatch, "We compare the tech one guide approach.", rows)
    t1 = _to(db, T1)
    assert len(t1) == 1 and t1[0][3] == "tech one guide"  # anchored only, no gap
    assert [p[3] for p in _to(db, T2)] == [None]  # T2 still gets its gap


def test_no_gap_to_a_hub_that_already_has_an_anchored_suggestion(monkeypatch):
    brief = {"primary_keyword": "pgvector", "secondary_keywords": []}
    db, _ = _run(monkeypatch, "We rely on pgvector for retrieval.", [], brief=brief)
    hub = [p for p in db.inserts if p[4] == VDB]
    assert len(hub) == 1 and hub[0][3] == "pgvector"


def test_no_gap_to_a_target_already_linked_in_the_body(monkeypatch):
    rows = [_row(T1, "Tech One", []), _row(T2, "Tech Two", [])]
    body = f"See [one](rf:article/{T1}) and the [hub]({MVP}) page."
    db, _ = _run(monkeypatch, body, rows)
    assert _to(db, T1) == []
    assert [p for p in db.inserts if p[4] == MVP] == []


# --- N1 (round 2): pending rows from EARLIER runs count, even when the re-insert
# of the anchored suggestion conflicts and returns None ---
def test_rerun_with_conflicting_anchor_stages_no_gap_to_that_target(monkeypatch):
    rows = [_row(T1, "Tech One", ["tech one guide"])]
    db = FakeDB(rows)
    db.pending.append({"target_article_id": T1, "anchor_text": "tech one guide",
                       "target_url": "https://powabase.ai/blog/tech-one",
                       "kind": "mention"})  # staged by an earlier run
    _, out = _run(monkeypatch, "We compare the tech one guide approach.", rows,
                  db=db)
    assert out == []  # the anchored re-insert conflicted
    assert [p[3] for p in _to(db, T1)] == ["tech one guide"]  # no gap attempted
    assert [r for r in db.pending if r["anchor_text"] is None] == []


def test_rerun_with_pending_hub_anchor_stages_no_hub_gap(monkeypatch):
    brief = {"primary_keyword": "pgvector", "secondary_keywords": []}
    db = FakeDB([])
    db.pending.append({"target_article_id": None, "anchor_text": "pgvector",
                       "target_url": VDB, "kind": "mention"})
    _run(monkeypatch, "We rely on pgvector for retrieval.", [], brief=brief, db=db)
    assert [p[3] for p in db.inserts if p[4] == VDB] == ["pgvector"]


def test_two_runs_stage_the_same_suggestions_once(monkeypatch):
    """min=3: run 1 stages one anchor (T1) and two gaps. Run 2 re-finds the same
    anchor (conflict) — it must neither gap T1 nor top up with gaps to new targets,
    because the three pending suggestions already reach the minimum."""
    rows = [_row(T1, "Tech One", ["tech one guide"]), _row(T2, "Tech Two", []),
            _row(T3, "Tech Three", [], "agents"), _row(T5, "Tech Five", [], "agents")]
    body = "We compare the tech one guide approach."
    db, first = _run(monkeypatch, body, rows, _brand(min=3, max=10))
    assert len(first) == 3 and len(db.pending) == 3
    before = list(db.pending)
    _, second = _run(monkeypatch, body, rows, _brand(min=3, max=10), db=db)
    assert second == []
    assert db.pending == before  # nothing new: no T1 gap, no fourth target
    assert [r["anchor_text"] for r in db.pending
            if str(r["target_article_id"]) == T1] == ["tech one guide"]


# --- hub gap-fill writes the hub URL, never a bogus article ref ---
def test_hub_gap_fill_never_writes_rf_article_none(monkeypatch):
    brief = {"primary_keyword": "pgvector", "secondary_keywords": []}
    db, _ = _run(monkeypatch, "Nothing matching here.", [], brief=brief)
    hub = [p for p in db.inserts if p[4] == VDB]
    assert len(hub) == 1
    assert hub[0][2] is None and hub[0][3] is None  # no target article; a gap
    assert not any("rf:article/None" in str(v) for p in db.inserts for v in p)


# --- caps and arithmetic ---
def test_suggestions_stop_at_the_profile_max(monkeypatch):
    rows = [_row(t, f"Tech {i}", [f"topic number {i}"])
            for i, t in enumerate([T1, T2, T3, T4], start=1)]
    body = "Talk about topic number 1, topic number 2, topic number 3, topic number 4."
    db, out = _run(monkeypatch, body, rows, _brand(min=0, max=2))
    assert len(out) == 2 and len(db.inserts) == 2


def test_min_gap_counts_existing_links_and_anchors(monkeypatch):
    """min=4; the body already links one article and one hub, and one new anchor is
    found → exactly 4-1-1-1 = 1 gap is staged, though two more candidates exist."""
    rows = [_row(T1, "Tech One", ["tech one guide"]), _row(T2, "Tech Two", []),
            _row(T3, "Tech Three", [], "agents"), _row(T5, "Tech Five", [], "agents"),
            _row(T4, "Linked", [], "enterprise")]
    body = (f"See [x](rf:article/{T4}) and [hub]({MVP}). "
            "Then the tech one guide.")
    db, _ = _run(monkeypatch, body, rows, _brand(min=4, max=10))
    gaps = [p for p in db.inserts if p[3] is None]
    assert len(gaps) == 1


def test_min_gap_zero_when_minimum_already_met(monkeypatch):
    rows = [_row(T1, "Tech One", []), _row(T2, "Tech Two", [])]
    body = (f"[a](rf:article/{T3}) [b](rf:article/{T4}) [c]({MVP})")
    db, _ = _run(monkeypatch, body, rows)  # min=3, three links already
    assert db.inserts == []


def test_two_hub_mentions_stage_two_hub_suggestions(monkeypatch):
    body = "We use pgvector as a vector database, and offer a free mvp build."
    db, _ = _run(monkeypatch, body, [], _brand(min=0))
    urls = sorted(p[4] for p in db.inserts)
    assert urls == [MVP, VDB]  # one per hub, even with two topics of the same hub


# --- ordering ---
def test_structural_link_is_considered_first(monkeypatch):
    """With room for one link, the cluster pillar wins over an earlier mention."""
    pillar = _row(PILLAR, "Pillar Guide", ["pillar topic"])
    rows = [_row(T1, "Tech One", ["tech one guide"])]
    body = "The tech one guide leads into the pillar topic."
    db, _ = _run(monkeypatch, body, rows, _brand(min=0, max=1),
                 cluster="c1", pillar=pillar)
    assert [str(p[2]) for p in db.inserts] == [PILLAR]
    assert db.inserts[0][7] == "pillar"


def test_link_candidates_structural_first_then_hubs_then_ranked(monkeypatch):
    pillar = _row(PILLAR, "Pillar Guide", [])
    rows = [
        _row(T1, "Low", ["unrelated"]),
        _row(T2, "High", ["pgvector index", "hnsw tuning"]),
        _row(T3, "Mid", ["pgvector"]),
    ]
    db = FakeDB(rows, pillar)
    art = {"id": AID, "business_id": BID, "cluster_id": "c1",
           "cluster_role": "member"}
    brief = {"primary_keyword": "pgvector",
             "secondary_keywords": ["pgvector index", "hnsw tuning"]}
    c = lk.link_candidates(db, _brand(), art, brief)
    targets = [x["target"] for x in c]
    assert targets[0] == f"rf:article/{PILLAR}"  # structural first
    assert targets[1] == VDB  # then the matching hub
    # Then published technical articles by keyword overlap, highest first (per-
    # category cap 2 → the lowest-ranked one is dropped).
    assert targets[2:] == [f"rf:article/{T2}", f"rf:article/{T3}"]


def test_canonical_override_gets_the_trailing_slash_on_profile_brands():
    art = {"slug": "a", "canonical_url": "https://powabase.ai/guides/a"}
    assert lk.canonical_url(_brand(), art) == "https://powabase.ai/guides/a/"
    legacy = {**_brand(), "blog_profile": None}
    assert lk.canonical_url(legacy, art) == "https://powabase.ai/guides/a"
