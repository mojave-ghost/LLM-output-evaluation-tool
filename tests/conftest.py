"""Shared test fixtures and one-time setup.

Import-order guarantees
-----------------------
All module-level code here runs before pytest collects or imports any test
file.  The sequence must be:

  1. Stub ``anthropic`` in sys.modules  (no real API calls ever made)
  2. Import src.database and patch its engine / SessionLocal to point at a
     temp SQLite file so tests never touch eval_tool.db.
  3. Import src.worker *after* step 2 so its ``from .database import
     SessionLocal`` picks up the patched sessionmaker.
  4. Create all tables on the test engine once per session.
"""

# ── 1. Stub out the Anthropic SDK before any src import ───────────────────
import sys
import types
import unittest.mock

_fake_anthropic = types.ModuleType("anthropic")
_fake_anthropic.Anthropic = unittest.mock.MagicMock
_fake_anthropic.APIConnectionError = Exception
_fake_anthropic.APIStatusError = Exception
sys.modules.setdefault("anthropic", _fake_anthropic)

# ── 2. Create test engine and patch src.database before worker import ─────
import atexit
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src import database as _db_mod
from src.database import Base

_tmp_db = tempfile.mktemp(suffix="_test.db")
atexit.register(lambda: os.unlink(_tmp_db) if os.path.exists(_tmp_db) else None)

_TEST_ENGINE = create_engine(
    f"sqlite:///{_tmp_db}",
    connect_args={"check_same_thread": False},
)

@event.listens_for(_TEST_ENGINE, "connect")
def _wal(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()

_TEST_SESSION = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)

# Patch database module *before* importing worker
_db_mod.engine = _TEST_ENGINE
_db_mod.SessionLocal = _TEST_SESSION

# ── 3. Import worker (picks up patched SessionLocal) ─────────────────────
import src.worker as _worker_mod
_worker_mod.SessionLocal = _TEST_SESSION     # fix local binding in worker

# ── 4. Create all tables once ─────────────────────────────────────────────
Base.metadata.create_all(_TEST_ENGINE)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset():
    """Truncate every table and reset in-process singletons after each test."""
    yield
    session = _TEST_SESSION()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
    finally:
        session.close()

    from src.routers.jobs import _dedup_store, _queue
    _queue._heap.clear()
    _queue.running = 0
    _dedup_store.clear()


@pytest.fixture
def db():
    session = _TEST_SESSION()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    """TestClient with get_db overridden; lifespan NOT triggered (no worker)."""
    from main import app
    from src.database import get_db

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    app.dependency_overrides.clear()


# ── Convenience fixtures ───────────────────────────────────────────────────

@pytest.fixture
def registered_user(client):
    """Register + login a user. Returns (email, password, token, headers)."""
    email, pw = "user@test.com", "password123"
    client.post("/auth/register", json={"email": email, "password": pw})
    resp = client.post("/auth/login", data={"username": email, "password": pw})
    token = resp.json()["access_token"]
    return email, pw, token, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def second_user(client):
    """A second distinct user for isolation tests."""
    email, pw = "other@test.com", "password456"
    client.post("/auth/register", json={"email": email, "password": pw})
    resp = client.post("/auth/login", data={"username": email, "password": pw})
    token = resp.json()["access_token"]
    return email, pw, token, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def seeded_rubric(db, registered_user):
    """Seed the default 3-dimension rubric. Returns (rubric_orm, [dim_orm])."""
    import uuid
    from datetime import datetime
    from src.database import RubricDimensionORM, RubricORM, UserORM

    email, *_ = registered_user
    user = db.query(UserORM).filter(UserORM.email == email).first()
    rubric = RubricORM(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Default",
        is_default=True,
        created_at=datetime.utcnow(),
    )
    db.add(rubric)
    db.flush()
    dims = []
    for name, desc, w in [
        ("Correctness", "Factual accuracy", 0.4),
        ("Relevance", "On-topic response", 0.3),
        ("Faithfulness", "Source fidelity", 0.3),
    ]:
        d = RubricDimensionORM(
            id=uuid.uuid4(),
            rubric_id=rubric.id,
            name=name,
            description=desc,
            weight=w,
        )
        db.add(d)
        dims.append(d)
    db.commit()
    return rubric, dims


def make_eval_job(db, user_id, rubric_id, response_text="test response", priority=1):
    """Insert an EvalJobORM row and return the matching EvalJob domain object."""
    import uuid
    from datetime import datetime
    from src.database import EvalJobORM
    from src.models.eval_job import EvalJob
    from src.models.job_status import JobStatus
    from src.services.dedup_cache import DedupCache

    job_id = uuid.uuid4()
    response_hash = DedupCache.hash_response(response_text)
    orm = EvalJobORM(
        id=job_id,
        owner_id=user_id,
        rubric_id=rubric_id,
        prompt="What is the answer?",
        response_text=response_text,
        response_hash=response_hash,
        priority=priority,
        status=JobStatus.QUEUED.value,
        retry_count=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(orm)
    db.commit()
    return EvalJob(
        id=job_id,
        owner_id=user_id,
        prompt="What is the answer?",
        response_text=response_text,
        response_hash=response_hash,
        rubric_id=rubric_id,
        priority=priority,
    )
