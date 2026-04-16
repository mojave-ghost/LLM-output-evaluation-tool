"""Job routes — POST /api/v1/jobs, GET /api/v1/jobs/{id}.

Requirements satisfied
----------------------
FR-001  Accept prompt + response_text + optional rubric_id
FR-002  SHA-256 hash on every submission; check dedup cache before enqueuing
FR-003  Cache hit → return previous result immediately (status: CACHED)
FR-004  Cache miss → enqueue job, return job_id + status: QUEUED
FR-005  Optional priority 0/1/2 (default 1); validated at schema level
FR-008  Status polling returns one of QUEUED/PROCESSING/COMPLETED/FAILED/CACHED
FR-023  User isolation — GET 404s on jobs the caller does not own
NFR-011 All DB access through SQLAlchemy ORM; no raw SQL
NFR-023 No stack traces in error responses

Singleton services
------------------
_queue       PriorityQueue — shared in-process heap (one process, CON-007)
_dedup_store dict[hash → result_id] — O(1) in-memory cache backed by
             DedupCacheORM (CON-006). Call hydrate_dedup_store(db) once at
             app startup so process restarts don't miss existing cache rows.
"""

import os
import uuid
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import (
    DedupCacheORM,
    EvalJobORM,
    EvalResultORM,
    RubricORM,
    get_db,
)
from ..models.eval_job import EvalJob
from ..models.job_status import JobStatus
from ..services.dedup_cache import DedupCache
from ..services.priority_queue import PriorityQueue
from .auth import UserORM, get_current_user

# ---------------------------------------------------------------------------
# Application-level singletons
# ---------------------------------------------------------------------------

_queue: PriorityQueue = PriorityQueue(
    max_workers=int(os.environ.get("MAX_WORKERS", "4"))
)

# in-process dedup dict — survives for the process lifetime; hydrated from DB
# on startup so prior cache entries are visible after a restart (CON-006)
_dedup_store: dict[str, uuid.UUID] = {}


def hydrate_dedup_store(db: Session) -> None:
    """Load every DEDUP_CACHE row into _dedup_store.

    Call once inside the FastAPI lifespan handler before accepting requests.
    Safe to call again (idempotent — just overwrites existing keys).
    """
    rows = db.query(DedupCacheORM).all()
    for row in rows:
        _dedup_store[row.response_hash] = row.result_id


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class JobSubmitRequest(BaseModel):
    prompt: str
    response_text: str
    rubric_id: Optional[uuid.UUID] = None
    priority: int = Field(default=1, ge=0, le=2, description="0=urgent 1=standard 2=background")

    model_config = {"str_strip_whitespace": True}


class DimensionScoreOut(BaseModel):
    dimension_id: str
    dimension_name: Optional[str]
    score: int
    rationale: str


class ResultOut(BaseModel):
    result_id: str
    composite_score: float
    dimension_scores: list[DimensionScoreOut]


class JobResponse(BaseModel):
    job_id: str
    status: str
    priority: int
    created_at: datetime
    updated_at: datetime
    result: Optional[ResultOut] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_result_out(result_orm: EvalResultORM) -> ResultOut:
    """Convert an EvalResultORM (with loaded relationships) to ResultOut."""
    dim_scores = [
        DimensionScoreOut(
            dimension_id=str(ds.dimension_id),
            dimension_name=ds.dimension.name if ds.dimension else None,
            score=ds.score,
            rationale=ds.rationale,
        )
        for ds in result_orm.dimension_scores
    ]
    return ResultOut(
        result_id=str(result_orm.id),
        composite_score=result_orm.composite_score,
        dimension_scores=dim_scores,
    )


def _resolve_rubric_id(requested: Optional[uuid.UUID], db: Session) -> uuid.UUID:
    """Return the rubric UUID to use for this job.

    Uses the caller-supplied rubric_id when provided, otherwise falls back to
    the system default rubric (is_default=True). Raises HTTP 422 / 404 on
    invalid or missing rubric (FR-001, FR-013).
    """
    if requested is not None:
        rubric = db.query(RubricORM).filter(RubricORM.id == requested).first()
        if rubric is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rubric not found.",
            )
        return rubric.id

    default = db.query(RubricORM).filter(RubricORM.is_default.is_(True)).first()
    if default is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No default rubric is configured. Provide a rubric_id.",
        )
    return default.id


def _lookup_dedup(response_hash: str, db: Session) -> Optional[uuid.UUID]:
    """Return a cached result_id for this hash, or None on miss.

    Checks the in-process dict first (O(1)), then falls back to the DB in
    case the entry was written by a previous process run (CON-006).
    Updates _dedup_store on a DB hit so subsequent calls skip the query.
    """
    result_id = _dedup_store.get(response_hash)
    if result_id is not None:
        return result_id

    row = db.query(DedupCacheORM).filter(
        DedupCacheORM.response_hash == response_hash
    ).first()
    if row is not None:
        _dedup_store[response_hash] = row.result_id
        return row.result_id

    return None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["jobs"])


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an LLM response for evaluation (FR-001–005)",
)
def submit_job(
    body: JobSubmitRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[UserORM, Depends(get_current_user)],
) -> JobResponse:
    """Submit a prompt + response pair for rubric-based evaluation.

    Flow (sequence diagram steps 1–4):
    1. Hash response_text with SHA-256 (FR-002).
    2. Check dedup cache. Cache hit → return prior result instantly, no re-
       queuing, HTTP 201 status=CACHED (FR-003).
    3. Cache miss → resolve rubric, persist EvalJobORM, push to PriorityQueue,
       return HTTP 201 status=QUEUED (FR-004).
    """
    # Step 1 — hash
    response_hash = DedupCache.hash_response(body.response_text)

    # Step 2 — dedup check (FR-002 / FR-003)
    cached_result_id = _lookup_dedup(response_hash, db)

    if cached_result_id is not None:
        result_orm = db.query(EvalResultORM).filter(
            EvalResultORM.id == cached_result_id
        ).first()

        # Persist a lightweight CACHED job record so the caller gets a
        # queryable job_id and the audit trail stays complete.
        now = datetime.utcnow()
        job_orm = EvalJobORM(
            id=uuid.uuid4(),
            owner_id=current_user.id,
            rubric_id=result_orm.rubric_id if result_orm else None,
            prompt=body.prompt,
            response_text=body.response_text,
            response_hash=response_hash,
            priority=body.priority,
            status=JobStatus.CACHED.value,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(job_orm)
        db.commit()
        db.refresh(job_orm)

        return JobResponse(
            job_id=str(job_orm.id),
            status=JobStatus.CACHED.value,
            priority=job_orm.priority,
            created_at=job_orm.created_at,
            updated_at=job_orm.updated_at,
            result=_build_result_out(result_orm) if result_orm else None,
        )

    # Step 3 — cache miss: resolve rubric, persist, enqueue (FR-004)
    rubric_id = _resolve_rubric_id(body.rubric_id, db)

    job_id = uuid.uuid4()
    now = datetime.utcnow()
    job_orm = EvalJobORM(
        id=job_id,
        owner_id=current_user.id,
        rubric_id=rubric_id,
        prompt=body.prompt,
        response_text=body.response_text,
        response_hash=response_hash,
        priority=body.priority,
        status=JobStatus.QUEUED.value,
        retry_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(job_orm)
    db.commit()
    db.refresh(job_orm)

    # Push domain object to queue — shares the same id as the ORM row so the
    # worker can UPDATE the correct record when scoring completes.
    eval_job = EvalJob(
        id=job_id,
        owner_id=current_user.id,
        prompt=body.prompt,
        response_text=body.response_text,
        response_hash=response_hash,
        rubric_id=rubric_id,
        priority=body.priority,
    )
    _queue.push(eval_job)

    return JobResponse(
        job_id=str(job_orm.id),
        status=JobStatus.QUEUED.value,
        priority=job_orm.priority,
        created_at=job_orm.created_at,
        updated_at=job_orm.updated_at,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Poll job status (FR-008)",
)
def get_job(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[UserORM, Depends(get_current_user)],
) -> JobResponse:
    """Return the current status of a job owned by the authenticated user.

    Returns QUEUED / PROCESSING / COMPLETED / FAILED / CACHED.
    When status is COMPLETED or CACHED the response includes the full result
    with per-dimension scores and rationale (FR-008, FR-019).
    Returns HTTP 404 for unknown IDs and for jobs owned by other users (FR-023).
    """
    try:
        parsed_id = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    job = (
        db.query(EvalJobORM)
        .filter(
            EvalJobORM.id == parsed_id,
            EvalJobORM.owner_id == current_user.id,  # FR-023: owner isolation
        )
        .first()
    )

    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    result_out: Optional[ResultOut] = None
    if job.status in (JobStatus.COMPLETED.value, JobStatus.CACHED.value) and job.result:
        result_out = _build_result_out(job.result)

    return JobResponse(
        job_id=str(job.id),
        status=job.status,
        priority=job.priority,
        created_at=job.created_at,
        updated_at=job.updated_at,
        result=result_out,
    )
