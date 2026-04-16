"""Unit and integration tests for the async worker (_process_job, worker_loop)."""

import asyncio
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.database import (
    DedupCacheORM,
    DimensionScoreORM,
    EvalJobORM,
    EvalResultORM,
    RubricORM,
    UserORM,
)
from src.models.dimension_score import DimensionScore
from src.models.eval_job import EvalJob
from src.models.eval_result import EvalResult
from src.models.job_status import JobStatus
from src.models.rubric_dimension import RubricDimension
from src.routers.jobs import _dedup_store, _queue
import src.worker as worker_mod
from src.services.dedup_cache import DedupCache


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_job(user_id, rubric_id, response_text="test response", priority=1):
    """Create and commit an EvalJobORM row in its own session. Returns EvalJob domain obj."""
    session = worker_mod.SessionLocal()
    try:
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
        session.add(orm)
        session.commit()
    finally:
        session.close()
    return EvalJob(
        id=job_id,
        owner_id=user_id,
        prompt="What is the answer?",
        response_text=response_text,
        response_hash=response_hash,
        rubric_id=rubric_id,
        priority=priority,
    )


def _fake_score_result(job, dims):
    """Build a realistic (EvalResult, [DimensionScore]) for mocking rubric_engine.score."""
    result_id = uuid.uuid4()
    result = EvalResult(id=result_id, job_id=job.id, rubric_id=job.rubric_id)
    dim_scores = [
        DimensionScore(result_id=result_id, dimension_id=d.id, score=4, rationale="Good.")
        for d in dims
    ]
    result.compute_composite(dim_scores, dims)
    return result, dim_scores


def _dim_objs(dims_orm):
    return [
        RubricDimension(
            id=d.id, rubric_id=d.rubric_id,
            name=d.name, description=d.description, weight=d.weight,
        )
        for d in dims_orm
    ]


# ── Fixture: IDs and dim domain objects available to worker tests ─────────────

@pytest.fixture
def worker_ctx(db, registered_user, seeded_rubric):
    """Return (user_id, rubric_id, dim_objs) with all setup committed and visible."""
    email, *_ = registered_user
    rubric, dims_orm = seeded_rubric
    user = db.query(UserORM).filter(UserORM.email == email).first()
    return user.id, rubric.id, _dim_objs(dims_orm)


# ── Success path ──────────────────────────────────────────────────────────────

class TestProcessJobSuccess:
    def test_job_marked_completed(self, worker_ctx):
        user_id, rubric_id, dim_objs = worker_ctx
        job = _seed_job(user_id, rubric_id)
        _queue.push(job)
        _queue.pop()

        with patch.object(worker_mod.rubric_engine, "score", return_value=_fake_score_result(job, dim_objs)):
            asyncio.run(worker_mod._process_job(job))

        session = worker_mod.SessionLocal()
        try:
            orm = session.query(EvalJobORM).filter(EvalJobORM.id == job.id).first()
            assert orm is not None
            assert orm.status == JobStatus.COMPLETED.value
        finally:
            session.close()

    def test_eval_result_row_created(self, worker_ctx):
        user_id, rubric_id, dim_objs = worker_ctx
        job = _seed_job(user_id, rubric_id)
        _queue.push(job)
        _queue.pop()

        with patch.object(worker_mod.rubric_engine, "score", return_value=_fake_score_result(job, dim_objs)):
            asyncio.run(worker_mod._process_job(job))

        session = worker_mod.SessionLocal()
        try:
            result = session.query(EvalResultORM).filter(EvalResultORM.job_id == job.id).first()
            assert result is not None
            assert result.composite_score is not None
        finally:
            session.close()

    def test_dimension_scores_written(self, worker_ctx):
        user_id, rubric_id, dim_objs = worker_ctx
        job = _seed_job(user_id, rubric_id)
        _queue.push(job)
        _queue.pop()

        with patch.object(worker_mod.rubric_engine, "score", return_value=_fake_score_result(job, dim_objs)):
            asyncio.run(worker_mod._process_job(job))

        session = worker_mod.SessionLocal()
        try:
            result = session.query(EvalResultORM).filter(EvalResultORM.job_id == job.id).first()
            assert result is not None
            scores = session.query(DimensionScoreORM).filter(
                DimensionScoreORM.result_id == result.id
            ).all()
            assert len(scores) == len(dim_objs)
        finally:
            session.close()

    def test_dedup_cache_row_written(self, worker_ctx):
        user_id, rubric_id, dim_objs = worker_ctx
        job = _seed_job(user_id, rubric_id)
        _queue.push(job)
        _queue.pop()

        with patch.object(worker_mod.rubric_engine, "score", return_value=_fake_score_result(job, dim_objs)):
            asyncio.run(worker_mod._process_job(job))

        session = worker_mod.SessionLocal()
        try:
            row = session.query(DedupCacheORM).filter(
                DedupCacheORM.response_hash == job.response_hash
            ).first()
            assert row is not None
        finally:
            session.close()

    def test_dedup_store_warmed_after_success(self, worker_ctx):
        user_id, rubric_id, dim_objs = worker_ctx
        job = _seed_job(user_id, rubric_id)
        _queue.push(job)
        _queue.pop()

        with patch.object(worker_mod.rubric_engine, "score", return_value=_fake_score_result(job, dim_objs)):
            asyncio.run(worker_mod._process_job(job))

        assert job.response_hash in _dedup_store

    def test_running_counter_decremented_after_success(self, worker_ctx):
        user_id, rubric_id, dim_objs = worker_ctx
        job = _seed_job(user_id, rubric_id)
        _queue.push(job)
        _queue.pop()
        running_before = _queue.running

        with patch.object(worker_mod.rubric_engine, "score", return_value=_fake_score_result(job, dim_objs)):
            asyncio.run(worker_mod._process_job(job))

        assert _queue.running == running_before - 1


# ── Failure / retry path ──────────────────────────────────────────────────────

class TestProcessJobRetry:
    def test_first_failure_re_queues_job(self, worker_ctx):
        user_id, rubric_id, _ = worker_ctx
        job = _seed_job(user_id, rubric_id)
        _queue.push(job)
        _queue.pop()

        with patch.object(worker_mod.rubric_engine, "score", side_effect=RuntimeError("down")):
            with patch("asyncio.sleep", return_value=None):
                asyncio.run(worker_mod._process_job(job))

        session = worker_mod.SessionLocal()
        try:
            orm = session.query(EvalJobORM).filter(EvalJobORM.id == job.id).first()
            assert orm.status == JobStatus.QUEUED.value
            assert orm.retry_count == 1
        finally:
            session.close()

    def test_third_failure_marks_permanently_failed(self, worker_ctx):
        user_id, rubric_id, _ = worker_ctx
        job = _seed_job(user_id, rubric_id)

        # Pre-set retry_count to 2 so next failure is the 3rd
        session = worker_mod.SessionLocal()
        try:
            orm = session.query(EvalJobORM).filter(EvalJobORM.id == job.id).first()
            orm.retry_count = 2
            session.commit()
        finally:
            session.close()
        job.retry_count = 2

        _queue.push(job)
        _queue.pop()

        with patch.object(worker_mod.rubric_engine, "score", side_effect=RuntimeError("still down")):
            asyncio.run(worker_mod._process_job(job))

        session = worker_mod.SessionLocal()
        try:
            orm = session.query(EvalJobORM).filter(EvalJobORM.id == job.id).first()
            assert orm.status == JobStatus.FAILED.value
            assert orm.retry_count == 3
        finally:
            session.close()

    def test_slot_released_before_backoff(self, worker_ctx):
        """NFR-009: worker slot freed during retry sleep so queue stays unblocked."""
        user_id, rubric_id, _ = worker_ctx
        job = _seed_job(user_id, rubric_id)
        _queue.push(job)
        _queue.pop()

        running_during_sleep = []

        async def _fake_sleep(secs):
            running_during_sleep.append(_queue.running)

        with patch.object(worker_mod.rubric_engine, "score", side_effect=RuntimeError("err")):
            with patch("asyncio.sleep", side_effect=_fake_sleep):
                asyncio.run(worker_mod._process_job(job))

        # Slot must be released (running == 0) before backoff sleep
        assert running_during_sleep and running_during_sleep[0] == 0


# ── No dimensions edge case ───────────────────────────────────────────────────

class TestProcessJobNoDimensions:
    def test_no_rubric_dims_marks_failed(self, db, registered_user):
        email, *_ = registered_user
        user = db.query(UserORM).filter(UserORM.email == email).first()

        # Rubric with no dimensions
        empty_rubric_id = uuid.uuid4()
        db.add(RubricORM(
            id=empty_rubric_id, owner_id=user.id,
            name="Empty", is_default=False, created_at=datetime.utcnow(),
        ))
        db.commit()

        job = _seed_job(user.id, empty_rubric_id)
        _queue.push(job)
        _queue.pop()

        asyncio.run(worker_mod._process_job(job))

        session = worker_mod.SessionLocal()
        try:
            orm = session.query(EvalJobORM).filter(EvalJobORM.id == job.id).first()
            assert orm.status == JobStatus.FAILED.value
        finally:
            session.close()


# ── Missing job ORM edge case ─────────────────────────────────────────────────

class TestProcessJobMissingORM:
    def test_missing_orm_row_skipped_gracefully(self, worker_ctx):
        user_id, rubric_id, _ = worker_ctx
        job = _seed_job(user_id, rubric_id)

        # Delete the job row so worker can't find it
        session = worker_mod.SessionLocal()
        try:
            session.query(EvalJobORM).filter(EvalJobORM.id == job.id).delete()
            session.commit()
        finally:
            session.close()

        _queue.push(job)
        _queue.pop()

        # Should not raise
        asyncio.run(worker_mod._process_job(job))


# ── Integration: full submit → worker → COMPLETED ─────────────────────────────

class TestWorkerIntegration:
    def test_full_pipeline_job_completed(self, worker_ctx):
        """Submit → enqueue → worker processes → COMPLETED + full result in DB."""
        user_id, rubric_id, dim_objs = worker_ctx
        job = _seed_job(user_id, rubric_id, response_text="integration response")
        _queue.push(job)
        _queue.pop()

        with patch.object(
            worker_mod.rubric_engine, "score",
            return_value=_fake_score_result(job, dim_objs),
        ):
            asyncio.run(worker_mod._process_job(job))

        session = worker_mod.SessionLocal()
        try:
            job_orm = session.query(EvalJobORM).filter(EvalJobORM.id == job.id).first()
            assert job_orm.status == JobStatus.COMPLETED.value

            result = session.query(EvalResultORM).filter(EvalResultORM.job_id == job.id).first()
            assert result is not None
            assert result.composite_score > 0

            scores = session.query(DimensionScoreORM).filter(
                DimensionScoreORM.result_id == result.id
            ).all()
            assert len(scores) == len(dim_objs)

            dedup = session.query(DedupCacheORM).filter(
                DedupCacheORM.response_hash == job.response_hash
            ).first()
            assert dedup is not None
            assert dedup.result_id == result.id
        finally:
            session.close()

        # In-process dedup store warmed
        assert job.response_hash in _dedup_store
        assert _dedup_store[job.response_hash] == result.id
