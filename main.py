"""FastAPI application entry point.

Startup sequence
----------------
1. lifespan opens a DB session and calls init_db() — creates tables (NFR-008
   WAL mode is enabled by the connect event registered in database.py).
2. hydrate_dedup_store() warms the in-process dedup dict from the
   DEDUP_CACHE table so process restarts don't lose cached entries (CON-006).
3. Routers are registered; every route except /auth/* requires a valid JWT
   (FR-022 enforced via the get_current_user dependency in each router).

NFR-019  GET /health returns queue depth + DB connectivity within 100 ms.
NFR-017  OpenAPI docs auto-generated at /docs and /redoc.
NFR-018  Structured JSON logging for startup events (stdlib logging).
"""

import logging
import os
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.database import SessionLocal, engine, init_db
from src.routers import auth, jobs, results, rubrics
from src.routers.jobs import _queue, hydrate_dedup_store
from src.worker import worker_loop

# ---------------------------------------------------------------------------
# Logging — structured enough for standard log aggregators (NFR-018)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "msg": "%(message)s"}',
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown (replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ── Startup ──────────────────────────────────────────────────────────────
    log.info("Starting up — creating tables")
    init_db()

    db = SessionLocal()
    try:
        log.info("Hydrating dedup cache from DB")
        hydrate_dedup_store(db)
    finally:
        db.close()

    # Start background worker task
    worker_task = asyncio.create_task(worker_loop())
    log.info("Startup complete")

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    log.info("Shutting down — cancelling worker")
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="LLM Output Evaluation Tool",
    description=(
        "Rubric-based evaluation of LLM responses using Claude as the judge. "
        "Submit jobs, track status, and browse scored results."
    ),
    version="1.0.0",
    lifespan=lifespan,
    # NFR-017: OpenAPI docs available at /docs and /redoc in all environments
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS — tighten ALLOW_ORIGINS in production via environment variable
# ---------------------------------------------------------------------------

_origins = os.environ.get("ALLOW_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)       # /auth/register, /auth/login, /auth/refresh
app.include_router(jobs.router)       # /api/v1/jobs
app.include_router(rubrics.router)    # /api/v1/rubrics
app.include_router(results.router)    # /api/v1/results

# ---------------------------------------------------------------------------
# Health endpoint (NFR-019)
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"], summary="System health check (NFR-019)")
def health() -> dict:
    """Return HTTP 200 with queue depth and DB connectivity status.

    Designed to respond within 100 ms so uptime monitors get a fast signal.
    DB check executes a single lightweight query; any exception sets
    db_status to 'error' without raising (degraded but not down).
    """
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    return {
        "status": "ok",
        "queue_depth": len(_queue),
        "db_status": db_status,
    }
