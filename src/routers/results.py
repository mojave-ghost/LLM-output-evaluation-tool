"""Results routes — GET /api/v1/results, GET /api/v1/results/{id}.

Requirements satisfied
----------------------
FR-015  Paginated results list: job_id, prompt, response, scores, rationale,
        rubric used, timestamp (AC-008: correct slice + total_count)
FR-017  Filter by date range, rubric_id, and min/max composite score
FR-019  Detail view with full per-dimension rationale
FR-023  User isolation — results are only returned for the caller's own jobs
NFR-011 All DB access through SQLAlchemy ORM; no raw SQL
NFR-023 No stack traces in error responses
"""

import uuid
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import EvalJobORM, EvalResultORM, get_db
from .auth import UserORM, get_current_user

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class DimensionScoreOut(BaseModel):
    dimension_id: str
    dimension_name: Optional[str]
    score: int
    rationale: str


class ResultOut(BaseModel):
    result_id: str
    job_id: str
    prompt: str
    response_text: str
    composite_score: float
    rubric_id: Optional[str]
    rubric_name: Optional[str]
    created_at: datetime
    dimension_scores: list[DimensionScoreOut]


class PaginatedResults(BaseModel):
    results: list[ResultOut]
    total_count: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _to_result_out(result: EvalResultORM) -> ResultOut:
    job = result.job
    return ResultOut(
        result_id=str(result.id),
        job_id=str(result.job_id),
        prompt=job.prompt if job else "",
        response_text=job.response_text if job else "",
        composite_score=result.composite_score,
        rubric_id=str(result.rubric_id) if result.rubric_id else None,
        rubric_name=result.rubric.name if result.rubric else None,
        created_at=result.created_at,
        dimension_scores=[
            DimensionScoreOut(
                dimension_id=str(ds.dimension_id),
                dimension_name=ds.dimension.name if ds.dimension else None,
                score=ds.score,
                rationale=ds.rationale,
            )
            for ds in result.dimension_scores
        ],
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["results"])


@router.get(
    "/results",
    response_model=PaginatedResults,
    summary="List evaluation results with filters (FR-015, FR-017)",
)
def list_results(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[UserORM, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Results per page")] = 10,
    rubric_id: Annotated[Optional[str], Query(description="Filter by rubric UUID")] = None,
    min_score: Annotated[Optional[float], Query(ge=0.0, le=5.0, description="Minimum composite score")] = None,
    max_score: Annotated[Optional[float], Query(ge=0.0, le=5.0, description="Maximum composite score")] = None,
    date_from: Annotated[Optional[datetime], Query(description="Earliest result timestamp (ISO 8601)")] = None,
    date_to: Annotated[Optional[datetime], Query(description="Latest result timestamp (ISO 8601)")] = None,
) -> PaginatedResults:
    """Return a paginated, filterable list of evaluation results.

    Only results whose parent job is owned by the authenticated user are
    returned (FR-023). Accepts any combination of filters — all are optional.

    Ordering: newest first (EvalResult.created_at DESC).
    """
    # Base query: join to EvalJobORM for owner isolation (FR-023)
    query = (
        db.query(EvalResultORM)
        .join(EvalJobORM, EvalResultORM.job_id == EvalJobORM.id)
        .filter(EvalJobORM.owner_id == current_user.id)
    )

    # Optional filters (FR-017)
    if rubric_id is not None:
        try:
            parsed_rubric_id = uuid.UUID(rubric_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="rubric_id must be a valid UUID.",
            )
        query = query.filter(EvalResultORM.rubric_id == parsed_rubric_id)

    if min_score is not None:
        query = query.filter(EvalResultORM.composite_score >= min_score)

    if max_score is not None:
        query = query.filter(EvalResultORM.composite_score <= max_score)

    if date_from is not None:
        query = query.filter(EvalResultORM.created_at >= date_from)

    if date_to is not None:
        query = query.filter(EvalResultORM.created_at <= date_to)

    # Total before pagination (AC-008)
    total_count = query.count()

    # Paginate (AC-008)
    offset = (page - 1) * page_size
    rows = (
        query
        .order_by(EvalResultORM.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return PaginatedResults(
        results=[_to_result_out(r) for r in rows],
        total_count=total_count,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/results/{result_id}",
    response_model=ResultOut,
    summary="Result detail with full per-dimension rationale (FR-019)",
)
def get_result(
    result_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[UserORM, Depends(get_current_user)],
) -> ResultOut:
    """Return full detail for a single evaluation result.

    Includes the chain-of-thought rationale for every dimension score
    (FR-019). Returns HTTP 404 for unknown IDs and for results belonging
    to another user's jobs (FR-023).
    """
    try:
        parsed_id = uuid.UUID(result_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found.")

    result = (
        db.query(EvalResultORM)
        .join(EvalJobORM, EvalResultORM.job_id == EvalJobORM.id)
        .filter(
            EvalResultORM.id == parsed_id,
            EvalJobORM.owner_id == current_user.id,  # FR-023
        )
        .first()
    )

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found.")

    return _to_result_out(result)
