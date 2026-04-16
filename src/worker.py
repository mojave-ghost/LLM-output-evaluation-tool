"""Async queue worker — drives jobs from QUEUED → PROCESSING → COMPLETED/FAILED.

Architecture
------------
worker_loop() is an asyncio.Task started in the FastAPI lifespan. It polls
the in-process PriorityQueue every POLL_INTERVAL seconds and spawns one
asyncio.Task per job up to MAX_WORKERS concurrency (FR-007).

Blocking Anthropic API calls inside RubricEngine are offloaded to a
ThreadPoolExecutor so the event loop stays responsive to HTTP requests.
All DB reads/writes happen in the asyncio thread — never inside the executor
thread — so SQLAlchemy sessions are single-threaded (NFR-011).

Requirements satisfied
----------------------
FR-007  Concurrent processing up to MAX_WORKERS
FR-008  DB status transitions: QUEUED → PROCESSING → COMPLETED / FAILED
FR-009  Retry up to 3× with exponential back-off; permanent FAILED after 3rd
FR-010  Rubric dimensions fetched from DB and passed to RubricEngine.score()
FR-011  Chain-of-thought rationale written to DimensionScore rows
NFR-007 Zero silent job loss — every failure is recorded in eval_jobs.status
NFR-009 API unavailability treated as retryable; slot released during backoff
        so the queue stays unblocked while waiting to retry
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .database import (
    DedupCacheORM,
    DimensionScoreORM,
    EvalJobORM,
    EvalResultORM,
    RubricDimensionORM,
    SessionLocal,
)
from .models.job_status import JobStatus
from .models.rubric_dimension import RubricDimension as RubricDimensionDomain
from .routers.jobs import _dedup_store, _queue
from .services.rubric_engine import RubricEngine

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL: float = float(os.environ.get("WORKER_POLL_INTERVAL", "0.2"))
MAX_RETRY_ATTEMPTS: int = 3  # FR-009

# ---------------------------------------------------------------------------
# Shared singletons (module-level so tests can swap them out)
# ---------------------------------------------------------------------------

rubric_engine: RubricEngine = RubricEngine()

_executor = ThreadPoolExecutor(
    max_workers=int(os.environ.get("MAX_WORKERS", "4")),
    thread_name_prefix="rubric-engine",
)

# ---------------------------------------------------------------------------
# Job processor
# ---------------------------------------------------------------------------

async def _process_job(job) -> None:  # job: EvalJob domain object
    """End-to-end processing pipeline for a single EvalJob.

    Success path (sequence diagram steps 4–5):
        pop → mark PROCESSING → score via RubricEngine → persist result
        → write dedup row → update _dedup_store → mark COMPLETED

    Failure path (FR-009):
        attempt < 3 → release slot, sleep 2^attempt s, re-push to queue
        attempt == 3 → mark FAILED permanently (NFR-007: never silently drop)
    """
    db = SessionLocal()
    # Tracks whether _queue.running was already decremented inside this
    # function so the finally block doesn't double-decrement.
    slot_released = False

    try:
        # ── 1. Fetch ORM row; mark PROCESSING in DB ─────────────────────────
        job_orm = db.query(EvalJobORM).filter(EvalJobORM.id == job.id).first()
        if job_orm is None:
            log.warning("worker: job %s not in DB — skipping", job.id)
            return

        job_orm.status = JobStatus.PROCESSING.value
        job_orm.updated_at = datetime.utcnow()
        db.commit()
        log.info("worker: job %s PROCESSING (priority=%d)", job.id, job.priority)

        # ── 2. Load rubric dimensions → domain objects ───────────────────────
        # Fetched here (asyncio thread) so the executor thread is DB-free.
        dims_orm = (
            db.query(RubricDimensionORM)
            .filter(RubricDimensionORM.rubric_id == job.rubric_id)
            .all()
        )
        if not dims_orm:
            job_orm.status = JobStatus.FAILED.value
            job_orm.updated_at = datetime.utcnow()
            db.commit()
            log.error(
                "worker: job %s FAILED — no dimensions for rubric %s",
                job.id, job.rubric_id,
            )
            return

        dimensions = [
            RubricDimensionDomain(
                id=d.id,
                rubric_id=d.rubric_id,
                name=d.name,
                description=d.description,
                weight=d.weight,
            )
            for d in dims_orm
        ]

        # ── 3. Call RubricEngine in thread pool (blocking I/O) ───────────────
        loop = asyncio.get_event_loop()
        try:
            result, dim_scores = await loop.run_in_executor(
                _executor, rubric_engine.score, job, dimensions
            )
        except Exception as exc:
            # ── Retry / permanent-failure logic (FR-009) ─────────────────────
            job_orm.retry_count += 1
            attempt = job_orm.retry_count

            if attempt >= MAX_RETRY_ATTEMPTS:
                job_orm.status = JobStatus.FAILED.value
                job_orm.updated_at = datetime.utcnow()
                db.commit()
                log.error(
                    "worker: job %s FAILED permanently after %d attempt(s) — %s",
                    job.id, attempt, exc,
                )
                return  # finally decrements running

            backoff = 2 ** attempt
            log.warning(
                "worker: job %s attempt %d/%d failed (%s); "
                "retrying in %ds (NFR-009)",
                job.id, attempt, MAX_RETRY_ATTEMPTS, exc, backoff,
            )
            job_orm.status = JobStatus.QUEUED.value
            job_orm.updated_at = datetime.utcnow()
            db.commit()

            # Release the worker slot *before* sleeping so MAX_WORKERS slots
            # are not consumed by jobs waiting on backoff (NFR-009).
            _queue.running = max(0, _queue.running - 1)
            slot_released = True

            await asyncio.sleep(backoff)

            # Re-push with updated retry_count; push() resets status → QUEUED
            job.retry_count = attempt
            _queue.push(job)
            return

        # ── 4. Persist EvalResult + DimensionScore rows ──────────────────────
        result_orm = EvalResultORM(
            id=result.id,
            job_id=job.id,
            rubric_id=job.rubric_id,
            composite_score=result.composite_score,
            created_at=result.created_at,
        )
        db.add(result_orm)
        db.flush()  # obtain result_orm.id before inserting child rows

        for ds in dim_scores:
            db.add(
                DimensionScoreORM(
                    id=ds.id,
                    result_id=result.id,
                    dimension_id=ds.dimension_id,
                    score=ds.score,
                    rationale=ds.rationale,
                )
            )

        # ── 5. Mark COMPLETED + write dedup cache row ────────────────────────
        job_orm.status = JobStatus.COMPLETED.value
        job_orm.updated_at = datetime.utcnow()

        db.add(
            DedupCacheORM(
                response_hash=job.response_hash,
                result_id=result.id,
                cached_at=datetime.utcnow(),
            )
        )
        db.commit()

        # Warm the in-process dedup store so subsequent identical submissions
        # hit the O(1) dict path rather than querying the DB (CON-006).
        _dedup_store[job.response_hash] = result.id

        log.info(
            "worker: job %s COMPLETED (composite=%.2f, dims=%d)",
            job.id, result.composite_score, len(dim_scores),
        )

    except Exception as exc:
        # Unexpected error outside the retry path — always fail safe (NFR-007).
        db.rollback()
        log.exception("worker: unexpected error on job %s — %s", job.id, exc)
        try:
            job_orm = db.query(EvalJobORM).filter(EvalJobORM.id == job.id).first()
            if job_orm and job_orm.status != JobStatus.FAILED.value:
                job_orm.status = JobStatus.FAILED.value
                job_orm.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass

    finally:
        if not slot_released:
            _queue.running = max(0, _queue.running - 1)
        db.close()


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

async def worker_loop() -> None:
    """Poll the PriorityQueue and dispatch jobs as asyncio Tasks (FR-007).

    Runs forever until cancelled by the FastAPI lifespan shutdown handler.
    Dispatches up to _queue.max_workers concurrent tasks; any remaining jobs
    stay in the heap until a slot frees up.
    """
    log.info(
        "worker: loop started (max_workers=%d, poll_interval=%.1fs)",
        _queue.max_workers,
        POLL_INTERVAL,
    )
    while True:
        try:
            # Drain as many jobs as free slots allow in this poll tick.
            while _queue.running < _queue.max_workers and len(_queue) > 0:
                job = _queue.pop()
                if job is not None:
                    asyncio.create_task(_process_job(job))
        except Exception as exc:
            log.error("worker: loop error — %s", exc)

        await asyncio.sleep(POLL_INTERVAL)
