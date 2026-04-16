"""Database layer — SQLAlchemy engine, ORM table mappings, and session factory.

Design decisions
----------------
* SQLite + WAL mode (NFR-008): enabled via a connect event so every connection
  issued by the pool executes PRAGMA journal_mode=WAL before use.
* ORM-only access (NFR-011): no raw SQL string interpolation anywhere in this
  module; all queries go through parameterised SQLAlchemy constructs.
* UUID storage: stored as CHAR(36) strings in SQLite (no native UUID type).
  Conversion between uuid.UUID and str is handled by the `UUIDStr` TypeDecorator.
* Constraints: CON-003 (SQLAlchemy + SQLite), CON-005 (SQLite only for v1.0).
"""

import uuid
from datetime import datetime
from typing import Generator

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker
from sqlalchemy.types import TypeDecorator

from .models.job_status import JobStatus

# ---------------------------------------------------------------------------
# Custom type: UUID ↔ CHAR(36) string
# ---------------------------------------------------------------------------

class UUIDStr(TypeDecorator):
    """Stores a Python uuid.UUID as a CHAR(36) string in SQLite."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return uuid.UUID(value)


# ---------------------------------------------------------------------------
# Engine + WAL mode (NFR-008)
# ---------------------------------------------------------------------------

DATABASE_URL = "sqlite:///./eval_tool.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # required for SQLite + threads
    echo=False,
)


@event.listens_for(engine, "connect")
def _enable_wal_mode(dbapi_connection, connection_record) -> None:
    """Enable WAL journal mode on every new connection (NFR-008)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ---------------------------------------------------------------------------
# Session factory + FastAPI dependency
# ---------------------------------------------------------------------------

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session, then close it — use as a FastAPI dependency.

    Example::

        @router.get("/jobs/{job_id}")
        def get_job(job_id: str, db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM table mappings
# ---------------------------------------------------------------------------

class UserORM(Base):
    """Maps to the USERS table (ERD)."""

    __tablename__ = "users"

    id = Column(UUIDStr, primary_key=True, default=uuid.uuid4)
    email = Column(Text, nullable=False, unique=True)
    hashed_pw = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    rubrics = relationship("RubricORM", back_populates="owner", cascade="all, delete-orphan")
    jobs = relationship("EvalJobORM", back_populates="owner", cascade="all, delete-orphan")


class RubricORM(Base):
    """Maps to the RUBRICS table (ERD)."""

    __tablename__ = "rubrics"

    id = Column(UUIDStr, primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUIDStr, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    owner = relationship("UserORM", back_populates="rubrics")
    dimensions = relationship("RubricDimensionORM", back_populates="rubric", cascade="all, delete-orphan")
    jobs = relationship("EvalJobORM", back_populates="rubric")
    results = relationship("EvalResultORM", back_populates="rubric")


class RubricDimensionORM(Base):
    """Maps to the RUBRIC_DIMENSIONS table (ERD)."""

    __tablename__ = "rubric_dimensions"

    id = Column(UUIDStr, primary_key=True, default=uuid.uuid4)
    rubric_id = Column(UUIDStr, ForeignKey("rubrics.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    weight = Column(Float, nullable=False)

    rubric = relationship("RubricORM", back_populates="dimensions")
    dimension_scores = relationship("DimensionScoreORM", back_populates="dimension")


class EvalJobORM(Base):
    """Maps to the EVAL_JOBS table (ERD)."""

    __tablename__ = "eval_jobs"

    id = Column(UUIDStr, primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUIDStr, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    rubric_id = Column(UUIDStr, ForeignKey("rubrics.id", ondelete="SET NULL"), nullable=True)
    prompt = Column(Text, nullable=False)
    response_text = Column(Text, nullable=False)
    response_hash = Column(String(64), nullable=False, index=True)  # SHA-256 hex = 64 chars
    priority = Column(Integer, nullable=False, default=1)           # 0=urgent 1=standard 2=background
    status = Column(String(16), nullable=False, default=JobStatus.QUEUED.value)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("UserORM", back_populates="jobs")
    rubric = relationship("RubricORM", back_populates="jobs")
    result = relationship("EvalResultORM", back_populates="job", uselist=False)


class EvalResultORM(Base):
    """Maps to the EVAL_RESULTS table (ERD)."""

    __tablename__ = "eval_results"

    id = Column(UUIDStr, primary_key=True, default=uuid.uuid4)
    job_id = Column(UUIDStr, ForeignKey("eval_jobs.id", ondelete="CASCADE"), nullable=False, unique=True)
    rubric_id = Column(UUIDStr, ForeignKey("rubrics.id", ondelete="SET NULL"), nullable=True)
    composite_score = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    job = relationship("EvalJobORM", back_populates="result")
    rubric = relationship("RubricORM", back_populates="results")
    dimension_scores = relationship("DimensionScoreORM", back_populates="result", cascade="all, delete-orphan")


class DimensionScoreORM(Base):
    """Maps to the DIMENSION_SCORES table (ERD)."""

    __tablename__ = "dimension_scores"

    id = Column(UUIDStr, primary_key=True, default=uuid.uuid4)
    result_id = Column(UUIDStr, ForeignKey("eval_results.id", ondelete="CASCADE"), nullable=False)
    dimension_id = Column(UUIDStr, ForeignKey("rubric_dimensions.id", ondelete="SET NULL"), nullable=True)
    score = Column(Integer, nullable=False)      # 1–5
    rationale = Column(Text, nullable=False)

    result = relationship("EvalResultORM", back_populates="dimension_scores")
    dimension = relationship("RubricDimensionORM", back_populates="dimension_scores")


class DedupCacheORM(Base):
    """Maps to the DEDUP_CACHE table (ERD).

    response_hash is the primary key (SHA-256 hex string).
    The in-process DedupCache service loads this table on startup so all
    lookups are O(1) in memory (CON-006).
    """

    __tablename__ = "dedup_cache"

    response_hash = Column(String(64), primary_key=True)
    result_id = Column(UUIDStr, ForeignKey("eval_results.id", ondelete="CASCADE"), nullable=False)
    cached_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they do not already exist.

    Call once at application startup (e.g. in the FastAPI lifespan handler).
    Safe to call repeatedly — uses CREATE TABLE IF NOT EXISTS semantics via
    SQLAlchemy's checkfirst behaviour.
    """
    Base.metadata.create_all(bind=engine)
