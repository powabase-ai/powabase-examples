"""The Powabase blog-profile seed script (imported, never run against a DB)."""

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest

from rankforge_backend.models.blog import BlogProfile

BRAND = "11111111-1111-1111-1111-111111111111"


def _load():
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "seed_powabase_blog_profile.py"
    spec = importlib.util.spec_from_file_location("seed_blog_profile_t", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_blog_profile_t"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeDB:
    def __init__(self, rows):
        self.rows, self.calls, self.rolled_back, self.closed = rows, [], False, False

    def open(self):
        pass

    def close(self):
        self.closed = True

    @contextmanager
    def connection(self):
        db = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, q, p):
                db.calls.append((q, p))

            def fetchall(self):
                return db.rows

        class _Conn:
            def cursor(self):
                return _Cur()

        try:
            yield _Conn()
        except BaseException:
            db.rolled_back = True
            raise


def _run(monkeypatch, rows, argv):
    seed = _load()
    fake = _FakeDB(rows)
    monkeypatch.setattr(seed, "Database", lambda *a, **k: fake)
    monkeypatch.setattr(seed, "get_settings", lambda: type("S", (), {
        "powabase_database_url": "postgresql://unused"})())
    return seed, fake, seed.main(argv)


def test_requires_a_brand_id(monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, [], [])
    assert e.value.code != 0
    with pytest.raises(SystemExit):
        _run(monkeypatch, [], ["--brand-id", "not-a-uuid"])


def test_writes_the_validated_dump_for_exactly_one_brand(monkeypatch):
    seed, fake, code = _run(
        monkeypatch, [{"id": BRAND, "name": "Powabase", "url_pattern": "x"}],
        ["--brand-id", BRAND],
    )
    assert code == 0 and not fake.rolled_back and fake.closed
    (q, p), = fake.calls
    assert "where id = %s" in q and "lower(name)" not in q
    assert p[0].obj == BlogProfile.model_validate(seed.PROFILE).model_dump()
    assert p[1] == UUID(BRAND)


@pytest.mark.parametrize("rows", [[], [{"id": 1}, {"id": 2}]])
def test_aborts_nonzero_and_rolls_back_unless_one_row(monkeypatch, rows):
    _, fake, code = _run(monkeypatch, rows, ["--brand-id", BRAND])
    assert code != 0
    assert fake.rolled_back and fake.closed
