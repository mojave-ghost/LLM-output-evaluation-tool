"""Rubric routes — POST /api/v1/rubrics, GET /api/v1/rubrics.

Requirements satisfied
----------------------
FR-013  POST creates a custom rubric with dimension names, descriptions, and
        weights; weights must sum to 1.0 (HTTP 422 with descriptive message
        on failure — AC-009)
FR-014  Rubrics persisted in SQLite and returned by GET for selection at job
        submission time
FR-023  Users may only list and manage their own rubrics; the system default
        rubric (is_default=True) is visible to all authenticated users
NFR-011 All DB access through SQLAlchemy ORM; no raw SQL
NFR-023 No stack traces in error responses
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..database import RubricDimensionORM, RubricORM, get_db
from .auth import UserORM, get_current_user

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

_WEIGHT_TOLERANCE = 1e-6  # float rounding headroom for sum-to-1 check


class DimensionIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    weight: float = Field(gt=0, le=1, description="Must be > 0 and <= 1")

    model_config = {"str_strip_whitespace": True}


class RubricCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    dimensions: list[DimensionIn] = Field(min_length=1)

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "RubricCreateRequest":
        """Reject the request if dimension weights do not sum to 1.0 (AC-009)."""
        names = [d.name for d in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("Dimension names must be unique within a rubric.")

        total = sum(d.weight for d in self.dimensions)
        if abs(total - 1.0) > _WEIGHT_TOLERANCE:
            raise ValueError(
                f"Dimension weights must sum to 1.0 (got {total:.6f}). "
                "Adjust your weights so they add up to exactly 1."
            )
        return self


class DimensionOut(BaseModel):
    id: str
    name: str
    description: str
    weight: float


class RubricResponse(BaseModel):
    id: str
    owner_id: str
    name: str
    is_default: bool
    created_at: datetime
    dimensions: list[DimensionOut]


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _to_response(rubric: RubricORM) -> RubricResponse:
    return RubricResponse(
        id=str(rubric.id),
        owner_id=str(rubric.owner_id),
        name=rubric.name,
        is_default=rubric.is_default,
        created_at=rubric.created_at,
        dimensions=[
            DimensionOut(
                id=str(d.id),
                name=d.name,
                description=d.description,
                weight=d.weight,
            )
            for d in rubric.dimensions
        ],
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["rubrics"])


@router.post(
    "/rubrics",
    response_model=RubricResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom rubric (FR-013)",
)
def create_rubric(
    body: RubricCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[UserORM, Depends(get_current_user)],
) -> RubricResponse:
    """Create a new evaluation rubric owned by the authenticated user.

    Dimension weights are validated by the request schema — any payload whose
    weights do not sum to 1.0 is rejected with HTTP 422 before touching the DB
    (AC-009, FR-013).
    """
    rubric_id = uuid.uuid4()
    now = datetime.utcnow()

    rubric = RubricORM(
        id=rubric_id,
        owner_id=current_user.id,
        name=body.name,
        is_default=False,  # user-created rubrics are never the system default
        created_at=now,
    )
    db.add(rubric)
    db.flush()  # obtain rubric.id before inserting dimensions

    for dim in body.dimensions:
        db.add(
            RubricDimensionORM(
                id=uuid.uuid4(),
                rubric_id=rubric_id,
                name=dim.name,
                description=dim.description,
                weight=dim.weight,
            )
        )

    db.commit()
    db.refresh(rubric)
    return _to_response(rubric)


@router.get(
    "/rubrics",
    response_model=list[RubricResponse],
    summary="List available rubrics (FR-014)",
)
def list_rubrics(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[UserORM, Depends(get_current_user)],
) -> list[RubricResponse]:
    """Return all rubrics available to the authenticated user.

    Includes:
    - Rubrics the user created (owner_id == current_user.id)
    - The system default rubric(s) (is_default=True), which are visible to
      all users because they are the fallback when no rubric_id is supplied
      at job submission time (FR-001, FR-014).

    Results are ordered: defaults first, then by creation time ascending.
    """
    rubrics = (
        db.query(RubricORM)
        .filter(
            (RubricORM.owner_id == current_user.id) | RubricORM.is_default.is_(True)
        )
        .order_by(RubricORM.is_default.desc(), RubricORM.created_at.asc())
        .all()
    )
    return [_to_response(r) for r in rubrics]
