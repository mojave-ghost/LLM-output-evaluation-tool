# LLM Output Evaluation Tool

Rubric-based evaluation of LLM responses using **Claude** as the judge.  
Submit a prompt + model response, get back a weighted composite score (1–5) with per-dimension chain-of-thought rationale.

---

## Features

- **Rubric-driven scoring** — define any number of weighted dimensions (weights must sum to 1.0); default rubric ships with Correctness × 0.4, Relevance × 0.3, Faithfulness × 0.3
- **Async evaluation queue** — in-process min-heap `PriorityQueue` (0 = urgent, 1 = standard, 2 = background); up to 4 concurrent worker slots by default
- **Deduplication cache** — SHA-256 hash of the response text; identical responses return a cached result instantly (status: `CACHED`) without re-calling Claude
- **Retry with exponential back-off** — up to 3 attempts on Claude API failure; permanently `FAILED` after the 3rd attempt
- **JWT auth** — bcrypt-hashed passwords, 15-minute access tokens + 7-day refresh tokens
- **Per-user data isolation** — jobs, results, and rubrics are scoped to the owner
- **React SPA frontend** — Dashboard, Submit, Results table (paginated + filtered), and Result detail views
- **OpenAPI docs** — auto-generated at `/docs` and `/redoc`
- **Health endpoint** — `GET /health` returns queue depth and DB status

---

## Architecture

```
┌─────────────┐   HTTP   ┌───────────────────────────────────────────┐
│  React SPA  │ ──────► │  FastAPI  (main.py)                        │
│  (Vite)     │          │  /auth/*   /api/v1/*   /health             │
└─────────────┘          └───────────────────┬───────────────────────┘
     :5173                                   │
                                ┌────────────┼────────────┐
                                ▼            ▼            ▼
                          PriorityQueue  DedupCache   SQLite
                          (in-process)   (in-process  (eval_tool.db
                                          + SQLite)    WAL mode)
                                │
                          worker_loop()
                          (asyncio Task)
                                │
                          RubricEngine
                          (Anthropic SDK
                           claude-sonnet-4-6
                           tool_use for
                           structured output)
```

**Submission flow** (matches UML sequence diagram):

1. Hash `response_text` with SHA-256
2. Dedup check — cache hit → return cached result immediately
3. Cache miss → persist `EvalJob`, push to `PriorityQueue`, return `job_id` + `QUEUED`
4. `worker_loop` pops job → `RubricEngine.score()` calls Claude once per dimension
5. Scores + rationale persisted; dedup cache warmed; job marked `COMPLETED`
6. Client polls `GET /api/v1/jobs/{id}` until terminal status

**Job lifecycle states:** `QUEUED` → `PROCESSING` → `COMPLETED` | `FAILED` | `CACHED`

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.11+, FastAPI, SQLAlchemy (ORM), Pydantic v2 |
| Database | SQLite (WAL mode), single file `eval_tool.db` |
| Auth | bcrypt (rounds=12), python-jose JWT |
| Judge LLM | Anthropic Claude (`claude-sonnet-4-6`) via `tool_use` |
| Frontend | React 18, React Router v6, Vite 5 |
| Testing | pytest, FastAPI TestClient, temp SQLite DB per session |

---

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- An [Anthropic API key](https://console.anthropic.com/)

---

## Setup

### 1. Backend

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install fastapi uvicorn sqlalchemy pydantic[email] python-jose bcrypt anthropic

# Set required environment variables
export ANTHROPIC_API_KEY="sk-ant-..."
export JWT_SECRET="change-me-in-production"

# Optional tuning
export MAX_WORKERS=4             # concurrent evaluation slots (default: 4)
export WORKER_POLL_INTERVAL=0.2  # seconds between queue polls (default: 0.2)
export ALLOW_ORIGINS="http://localhost:5173"  # CORS origins (default: *)
```

### 2. Frontend

```bash
cd frontend
npm install
```

---

## Running

### Backend

```bash
# From the project root (with .venv activated)
uvicorn main:app --reload --port 8000
```

The API is available at `http://localhost:8000`.  
Interactive API docs: `http://localhost:8000/docs`

### Frontend

```bash
cd frontend
npm run dev
```

The React app is available at `http://localhost:5173`.  
In development, Vite proxies `/api`, `/auth`, and `/health` to the backend — no CORS configuration needed.

---

## API Reference

All protected endpoints require `Authorization: Bearer <access_token>`.

### Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/register` | Create account (`{ email, password }`) |
| `POST` | `/auth/login` | Login — form-encoded; returns `{ access_token, refresh_token }` |
| `POST` | `/auth/refresh` | Exchange refresh token for new token pair |

### Jobs

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/jobs` | Submit a job `{ prompt, response_text, rubric_id?, priority? }` |
| `GET` | `/api/v1/jobs/{id}` | Poll job status; includes result when `COMPLETED` or `CACHED` |

**Priority values:** `0` = urgent, `1` = standard (default), `2` = background

### Rubrics

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/rubrics` | Create a custom rubric (weights must sum to 1.0) |
| `GET` | `/api/v1/rubrics` | List own rubrics + system defaults |

### Results

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/results` | Paginated results list; filter by `rubric_id`, `min_score`, `max_score`, `date_from`, `date_to` |
| `GET` | `/api/v1/results/{id}` | Full result detail with per-dimension rationale |

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns `{ status, queue_depth, db_status }` |

---

## Database Schema

Six SQLite tables (see `UML_ERD.pdf` for the full diagram):

```
USERS ──owns──► RUBRICS ──has──► RUBRIC_DIMENSIONS
  │                                      │
  └──submits──► EVAL_JOBS ──produces──► EVAL_RESULTS ──contains──► DIMENSION_SCORES
                                              │
                                        DEDUP_CACHE (keyed by SHA-256 hash)
```

WAL journal mode is enabled on every connection for concurrent read performance.

---

## Testing

Tests use an in-process SQLite database and stub out the Anthropic SDK — no real API calls are made.

```bash
# From the project root (with .venv activated)
pytest
```

Coverage is configured in `setup.cfg` (source: `src/`, minimum 80%).

```bash
pytest --cov=src --cov-report=term-missing
```

Test modules:

| File | Coverage |
|---|---|
| `tests/test_auth.py` | Register, login, refresh, JWT guards |
| `tests/test_jobs.py` | Submit (cache hit/miss), status polling, isolation |
| `tests/test_results.py` | List (pagination, filters), detail, isolation |
| `tests/test_rubrics.py` | Create (weight validation), list |
| `tests/test_models.py` | Domain model unit tests |
| `tests/test_services.py` | DedupCache, PriorityQueue, RubricEngine |
| `tests/test_worker.py` | Worker success, retry, failure, full pipeline |

---

## Project Structure

```
.
├── main.py                   # FastAPI app entry point + lifespan
├── src/
│   ├── database.py           # SQLAlchemy engine, ORM mappings, session factory
│   ├── worker.py             # Async queue worker loop
│   ├── models/               # Domain model dataclasses
│   │   ├── eval_job.py
│   │   ├── eval_result.py
│   │   ├── dimension_score.py
│   │   ├── rubric.py
│   │   ├── rubric_dimension.py
│   │   ├── user.py
│   │   └── job_status.py
│   ├── routers/              # FastAPI route handlers
│   │   ├── auth.py           # /auth/*
│   │   ├── jobs.py           # /api/v1/jobs
│   │   ├── rubrics.py        # /api/v1/rubrics
│   │   └── results.py        # /api/v1/results
│   └── services/
│       ├── rubric_engine.py  # Claude judge integration
│       ├── priority_queue.py # Min-heap job scheduler
│       └── dedup_cache.py    # SHA-256 dedup with SQLite backing
├── tests/
│   ├── conftest.py           # Shared fixtures, test DB setup
│   └── test_*.py
├── frontend/
│   ├── src/
│   │   ├── App.jsx           # Router + auth guards
│   │   ├── api/index.js      # Centralised API client
│   │   ├── context/AuthContext.jsx
│   │   ├── components/       # Nav, ScoreBar, StatusBadge
│   │   └── pages/            # Dashboard, SubmissionForm, ResultsTable, ResultDetail, Login, Register
│   ├── vite.config.js        # Dev proxy to :8000
│   └── package.json
├── UML_Class_Diagram.pdf
├── UML_ERD.pdf
├── UML_Sequence_Diagram.pdf
├── UML_State_Diagram.pdf
├── SRS_LLM_Eval_Tool.docx
└── wireframe_*.html          # UI wireframes
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Anthropic API key for Claude |
| `JWT_SECRET` | `change-me-before-production` | HS256 signing secret — **change in production** |
| `MAX_WORKERS` | `4` | Max concurrent evaluation jobs |
| `WORKER_POLL_INTERVAL` | `0.2` | Seconds between queue polls |
| `ALLOW_ORIGINS` | `*` | Comma-separated CORS allowed origins |
