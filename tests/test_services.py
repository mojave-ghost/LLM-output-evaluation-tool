"""Unit tests for service layer: DedupCache, PriorityQueue, RubricEngine."""

import hashlib
import os
import tempfile
import uuid
from unittest.mock import MagicMock

import pytest

from src.models.dimension_score import DimensionScore
from src.models.eval_job import EvalJob
from src.models.eval_result import EvalResult
from src.models.rubric_dimension import RubricDimension
from src.services.dedup_cache import DedupCache
from src.services.priority_queue import PriorityQueue
from src.services.rubric_engine import RubricEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_job(priority=1):
    return EvalJob(
        owner_id=uuid.uuid4(),
        prompt="What is 2+2?",
        response_text="4",
        response_hash=DedupCache.hash_response("4"),
        rubric_id=uuid.uuid4(),
        priority=priority,
    )


def _make_dim(weight=1.0):
    return RubricDimension(
        id=uuid.uuid4(),
        rubric_id=uuid.uuid4(),
        name="Correctness",
        description="Is the answer correct?",
        weight=weight,
    )


# ── DedupCache ────────────────────────────────────────────────────────────────

class TestDedupCache:
    @pytest.fixture
    def cache(self):
        db_path = tempfile.mktemp(suffix="_dedup_test.db")
        c = DedupCache(db_path=db_path)
        yield c
        c.backing.close()
        if os.path.exists(db_path):
            os.unlink(db_path)

    def test_hash_response_is_sha256(self):
        text = "hello world"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert DedupCache.hash_response(text) == expected

    def test_hash_response_is_64_hex_chars(self):
        h = DedupCache.hash_response("any text")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_response_deterministic(self):
        assert DedupCache.hash_response("abc") == DedupCache.hash_response("abc")

    def test_get_miss_returns_none(self, cache):
        assert cache.get("nonexistent_hash") is None

    def test_set_then_get_returns_uuid(self, cache):
        h = DedupCache.hash_response("my response")
        rid = uuid.uuid4()
        cache.set(h, rid)
        assert cache.get(h) == rid

    def test_set_overwrites_existing(self, cache):
        h = DedupCache.hash_response("same text")
        rid1, rid2 = uuid.uuid4(), uuid.uuid4()
        cache.set(h, rid1)
        cache.set(h, rid2)
        assert cache.get(h) == rid2

    def test_persistence_across_instances(self):
        db_path = tempfile.mktemp(suffix="_dedup_persist.db")
        try:
            h = DedupCache.hash_response("persistent text")
            rid = uuid.uuid4()

            c1 = DedupCache(db_path=db_path)
            c1.set(h, rid)
            c1.backing.close()

            c2 = DedupCache(db_path=db_path)
            assert c2.get(h) == rid
            c2.backing.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_get_after_only_db_entry(self):
        """New instance loads entries from DB into memory on init."""
        db_path = tempfile.mktemp(suffix="_dedup_load.db")
        try:
            h = DedupCache.hash_response("load from db")
            rid = uuid.uuid4()

            c1 = DedupCache(db_path=db_path)
            c1.set(h, rid)
            c1.backing.close()

            c2 = DedupCache(db_path=db_path)
            # Should be in memory (loaded via _load_from_db)
            assert c2._store.get(h) == rid
            c2.backing.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


# ── PriorityQueue ─────────────────────────────────────────────────────────────

class TestPriorityQueue:
    def test_len_empty(self):
        q = PriorityQueue()
        assert len(q) == 0

    def test_len_after_push(self):
        q = PriorityQueue()
        q.push(_make_job(priority=1))
        assert len(q) == 1

    def test_pop_empty_returns_none(self):
        q = PriorityQueue()
        assert q.pop() is None

    def test_push_sets_job_queued(self):
        from src.models.job_status import JobStatus
        q = PriorityQueue()
        job = _make_job()
        q.push(job)
        assert job.status == JobStatus.QUEUED

    def test_pop_sets_job_processing_and_increments_running(self):
        from src.models.job_status import JobStatus
        q = PriorityQueue()
        job = _make_job()
        q.push(job)
        assert q.running == 0
        popped = q.pop()
        assert popped is job
        assert popped.status == JobStatus.PROCESSING
        assert q.running == 1

    def test_priority_0_before_1_before_2(self):
        """FR-006: urgent (0) beats standard (1) beats background (2)."""
        q = PriorityQueue()
        j2 = _make_job(priority=2)
        j1 = _make_job(priority=1)
        j0 = _make_job(priority=0)
        # Push in reverse order to ensure heap sorts, not insertion order
        q.push(j2)
        q.push(j1)
        q.push(j0)

        assert q.pop() is j0
        assert q.pop() is j1
        assert q.pop() is j2

    def test_fifo_within_same_priority(self):
        """Jobs at the same priority are served in insertion order (FIFO)."""
        q = PriorityQueue()
        jobs = [_make_job(priority=1) for _ in range(3)]
        for j in jobs:
            q.push(j)
        assert q.pop() is jobs[0]
        assert q.pop() is jobs[1]
        assert q.pop() is jobs[2]

    def test_pop_skips_non_queued_jobs(self):
        """If a job was cancelled/modified after push, pop skips it."""
        from src.models.job_status import JobStatus
        q = PriorityQueue()
        job = _make_job()
        q.push(job)
        # Simulate job being cancelled externally
        job.status = JobStatus.FAILED
        assert q.pop() is None

    def test_max_workers_default(self):
        q = PriorityQueue()
        assert q.max_workers == 4

    def test_max_workers_custom(self):
        q = PriorityQueue(max_workers=2)
        assert q.max_workers == 2

    def test_running_initialises_to_zero(self):
        q = PriorityQueue()
        assert q.running == 0


# ── RubricEngine ──────────────────────────────────────────────────────────────

class TestRubricEngine:
    def test_score_returns_eval_result_and_dim_scores(self, monkeypatch):
        engine = RubricEngine()
        dim = _make_dim(weight=1.0)
        job = _make_job()
        monkeypatch.setattr(engine, "_call_judge", lambda p, r, d: (5, "perfect"))

        result, dim_scores = engine.score(job, [dim])

        assert isinstance(result, EvalResult)
        assert len(dim_scores) == 1
        assert dim_scores[0].score == 5
        assert dim_scores[0].rationale == "perfect"
        assert dim_scores[0].dimension_id == dim.id

    def test_score_computes_composite(self, monkeypatch):
        engine = RubricEngine()
        d1 = _make_dim(weight=0.4)
        d2 = _make_dim(weight=0.6)
        job = _make_job()

        call_count = [0]

        def fake_judge(prompt, response, dim):
            call_count[0] += 1
            return (5, "r") if dim.id == d1.id else (2, "r")

        monkeypatch.setattr(engine, "_call_judge", fake_judge)
        result, _ = engine.score(job, [d1, d2])

        # 5*0.4 + 2*0.6 = 2.0 + 1.2 = 3.2
        assert abs(result.composite_score - 3.2) < 1e-9
        assert call_count[0] == 2

    def test_score_propagates_judge_error(self, monkeypatch):
        engine = RubricEngine()
        job = _make_job()
        monkeypatch.setattr(
            engine, "_call_judge", lambda p, r, d: (_ for _ in ()).throw(RuntimeError("API down"))
        )
        with pytest.raises(RuntimeError, match="API down"):
            engine.score(job, [_make_dim()])

    def test_score_result_id_matches_dim_score_result_id(self, monkeypatch):
        engine = RubricEngine()
        monkeypatch.setattr(engine, "_call_judge", lambda p, r, d: (3, "ok"))
        job = _make_job()
        result, dim_scores = engine.score(job, [_make_dim(), _make_dim(weight=0.5)])
        for ds in dim_scores:
            assert ds.result_id == result.id

    def test_call_judge_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        engine = RubricEngine()
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            engine._call_judge("prompt", "response", _make_dim())

    def test_call_judge_with_mocked_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        block = MagicMock()
        block.type = "tool_use"
        block.name = "record_score"
        block.input = {"score": 4, "rationale": "Looks good"}

        mock_response = MagicMock()
        mock_response.content = [block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

        engine = RubricEngine()
        score, rationale = engine._call_judge("prompt", "response", _make_dim())
        assert score == 4
        assert rationale == "Looks good"

    def test_call_judge_raises_if_no_tool_use_block(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        mock_response = MagicMock()
        mock_response.content = []  # no tool_use block

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

        engine = RubricEngine()
        with pytest.raises(RuntimeError, match="record_score"):
            engine._call_judge("prompt", "response", _make_dim())

    def test_call_judge_raises_on_out_of_range_score(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        block = MagicMock()
        block.type = "tool_use"
        block.name = "record_score"
        block.input = {"score": 6, "rationale": "too high"}

        mock_response = MagicMock()
        mock_response.content = [block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

        engine = RubricEngine()
        with pytest.raises(RuntimeError, match="out-of-range"):
            engine._call_judge("prompt", "response", _make_dim())

    def test_call_judge_raises_on_api_connection_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        import anthropic
        mock_client = MagicMock()
        # The fake anthropic stub sets APIConnectionError = Exception; just instantiate directly
        mock_client.messages.create.side_effect = anthropic.APIConnectionError("connection refused")
        monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

        engine = RubricEngine()
        with pytest.raises(RuntimeError, match="unreachable"):
            engine._call_judge("prompt", "response", _make_dim())
