"""Integration tests for POST /api/v1/jobs and GET /api/v1/jobs/{id}."""

import uuid

import pytest

from src.database import DedupCacheORM, EvalJobORM, EvalResultORM
from src.models.job_status import JobStatus
from src.routers.jobs import _dedup_store


# ── Submit job (cache miss) ───────────────────────────────────────────────────

class TestSubmitJobCacheMiss:
    def test_submit_returns_201_queued(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "What is 2+2?", "response_text": "4"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "QUEUED"
        assert "job_id" in data

    def test_submit_persists_job_in_db(self, client, registered_user, seeded_rubric, db):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A"},
            headers=headers,
        )
        job_id = uuid.UUID(resp.json()["job_id"])
        orm = db.query(EvalJobORM).filter(EvalJobORM.id == job_id).first()
        assert orm is not None
        assert orm.status == JobStatus.QUEUED.value

    def test_submit_with_explicit_priority(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A", "priority": 0},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["priority"] == 0

    def test_submit_invalid_priority_returns_422(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A", "priority": 5},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_submit_unknown_rubric_returns_404(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A", "rubric_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_submit_no_default_rubric_returns_422(self, client, registered_user, db):
        """No default rubric and no rubric_id → 422."""
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_submit_with_explicit_rubric_id(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        rubric, _ = seeded_rubric
        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A", "rubric_id": str(rubric.id)},
            headers=headers,
        )
        assert resp.status_code == 201

    def test_submit_unauthenticated_returns_401(self, client, seeded_rubric):
        resp = client.post("/api/v1/jobs", json={"prompt": "Q", "response_text": "A"})
        assert resp.status_code == 401


# ── Submit job (cache hit) ────────────────────────────────────────────────────

class TestSubmitJobCacheHit:
    def test_cache_hit_returns_cached_status(self, client, registered_user, seeded_rubric, db):
        _, _, _, headers = registered_user
        rubric, _ = seeded_rubric

        # Manually seed a dedup cache entry
        response_text = "cached response text"
        from src.services.dedup_cache import DedupCache
        h = DedupCache.hash_response(response_text)

        # Create a result row for the cache to point at
        result_id = uuid.uuid4()
        job_id = uuid.uuid4()
        from datetime import datetime
        from src.database import UserORM
        email, *_ = registered_user
        user = db.query(UserORM).filter(UserORM.email == email).first()

        job_orm = EvalJobORM(
            id=job_id,
            owner_id=user.id,
            rubric_id=rubric.id,
            prompt="orig",
            response_text=response_text,
            response_hash=h,
            priority=1,
            status=JobStatus.COMPLETED.value,
            retry_count=0,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(job_orm)
        db.flush()  # ensure job_orm.id exists before result references it
        result_orm = EvalResultORM(
            id=result_id,
            job_id=job_id,
            rubric_id=rubric.id,
            composite_score=4.5,
            created_at=datetime.utcnow(),
        )
        db.add(result_orm)
        db.flush()  # ensure result_orm.id exists before dedup references it
        db.add(DedupCacheORM(response_hash=h, result_id=result_id, cached_at=datetime.utcnow()))
        db.commit()

        # Warm the in-process store (mimics hydrate_dedup_store)
        _dedup_store[h] = result_id

        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "new prompt", "response_text": response_text},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "CACHED"

    def test_cache_hit_without_in_memory_uses_db_fallback(self, client, registered_user, seeded_rubric, db):
        """_lookup_dedup falls back to DB when hash not in _dedup_store dict."""
        _, _, _, headers = registered_user
        rubric, _ = seeded_rubric

        from src.services.dedup_cache import DedupCache
        from src.database import UserORM
        from datetime import datetime

        response_text = "db fallback text"
        h = DedupCache.hash_response(response_text)

        email, *_ = registered_user
        user = db.query(UserORM).filter(UserORM.email == email).first()

        job_id = uuid.uuid4()
        result_id = uuid.uuid4()
        db.add(EvalJobORM(
            id=job_id, owner_id=user.id, rubric_id=rubric.id,
            prompt="orig", response_text=response_text, response_hash=h,
            priority=1, status=JobStatus.COMPLETED.value, retry_count=0,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        db.flush()
        db.add(EvalResultORM(
            id=result_id, job_id=job_id, rubric_id=rubric.id,
            composite_score=3.0, created_at=datetime.utcnow(),
        ))
        db.flush()
        db.add(DedupCacheORM(response_hash=h, result_id=result_id, cached_at=datetime.utcnow()))
        db.commit()

        # Do NOT warm _dedup_store — force DB fallback
        _dedup_store.pop(h, None)

        resp = client.post(
            "/api/v1/jobs",
            json={"prompt": "new prompt", "response_text": response_text},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "CACHED"


# ── Status polling ────────────────────────────────────────────────────────────

class TestGetJob:
    def test_get_queued_job_returns_200(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        submit = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A"},
            headers=headers,
        )
        job_id = submit.json()["job_id"]
        resp = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "QUEUED"

    def test_get_nonexistent_job_returns_404(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404

    def test_get_invalid_uuid_returns_404(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.get("/api/v1/jobs/not-a-uuid", headers=headers)
        assert resp.status_code == 404

    def test_get_other_users_job_returns_404(self, client, registered_user, second_user, seeded_rubric):
        """FR-023: user isolation — other user's job appears as 404."""
        _, _, _, headers1 = registered_user
        _, _, _, headers2 = second_user

        submit = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A"},
            headers=headers1,
        )
        job_id = submit.json()["job_id"]

        resp = client.get(f"/api/v1/jobs/{job_id}", headers=headers2)
        assert resp.status_code == 404

    def test_get_unauthenticated_returns_401(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        submit = client.post(
            "/api/v1/jobs",
            json={"prompt": "Q", "response_text": "A"},
            headers=headers,
        )
        job_id = submit.json()["job_id"]
        resp = client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 401

    def test_get_completed_job_includes_result(self, client, registered_user, seeded_rubric, db):
        """COMPLETED job response includes result payload (FR-008)."""
        from datetime import datetime
        from src.database import UserORM, DimensionScoreORM

        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        email, *_ = registered_user
        user = db.query(UserORM).filter(UserORM.email == email).first()

        # Seed COMPLETED job + result
        job_id = uuid.uuid4()
        result_id = uuid.uuid4()
        db.add(EvalJobORM(
            id=job_id, owner_id=user.id, rubric_id=rubric.id,
            prompt="P", response_text="R",
            response_hash="hash123", priority=1,
            status=JobStatus.COMPLETED.value, retry_count=0,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        db.add(EvalResultORM(
            id=result_id, job_id=job_id, rubric_id=rubric.id,
            composite_score=4.2, created_at=datetime.utcnow(),
        ))
        for dim in dims:
            db.add(DimensionScoreORM(
                id=uuid.uuid4(), result_id=result_id, dimension_id=dim.id,
                score=4, rationale="Good.",
            ))
        db.commit()

        resp = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["result"] is not None
        assert abs(data["result"]["composite_score"] - 4.2) < 1e-6
