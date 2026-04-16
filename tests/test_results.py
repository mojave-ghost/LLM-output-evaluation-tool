"""Integration tests for GET /api/v1/results and GET /api/v1/results/{id}."""

import uuid
from datetime import datetime

import pytest

from src.database import DimensionScoreORM, EvalJobORM, EvalResultORM, UserORM
from src.models.job_status import JobStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_result(db, user_id, rubric_id, dims, composite_score=3.5, response_text="resp"):
    """Insert a COMPLETED job + result + dimension scores. Returns (job_orm, result_orm)."""
    from src.services.dedup_cache import DedupCache
    job_id = uuid.uuid4()
    result_id = uuid.uuid4()
    now = datetime.utcnow()

    job = EvalJobORM(
        id=job_id, owner_id=user_id, rubric_id=rubric_id,
        prompt="Test prompt", response_text=response_text,
        response_hash=DedupCache.hash_response(response_text + str(uuid.uuid4())),
        priority=1, status=JobStatus.COMPLETED.value,
        retry_count=0, created_at=now, updated_at=now,
    )
    result = EvalResultORM(
        id=result_id, job_id=job_id, rubric_id=rubric_id,
        composite_score=composite_score, created_at=now,
    )
    db.add(job)
    db.add(result)
    for dim in dims:
        db.add(DimensionScoreORM(
            id=uuid.uuid4(), result_id=result_id, dimension_id=dim.id,
            score=round(composite_score), rationale="Test rationale.",
        ))
    db.commit()
    return job, result


# ── List results ──────────────────────────────────────────────────────────────

class TestListResults:
    def test_list_empty_for_new_user(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.get("/api/v1/results", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total_count"] == 0

    def test_list_returns_own_results(self, client, registered_user, seeded_rubric, db):
        email, *_ = registered_user
        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        user = db.query(UserORM).filter(UserORM.email == email).first()
        _seed_result(db, user.id, rubric.id, dims)

        resp = client.get("/api/v1/results", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total_count"] == 1

    def test_list_pagination_page_size(self, client, registered_user, seeded_rubric, db):
        email, *_ = registered_user
        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        user = db.query(UserORM).filter(UserORM.email == email).first()

        for i in range(5):
            _seed_result(db, user.id, rubric.id, dims, response_text=f"resp{i}")

        resp = client.get("/api/v1/results?page=1&page_size=2", headers=headers)
        data = resp.json()
        assert data["total_count"] == 5      # AC-008: full count
        assert len(data["results"]) == 2
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_list_pagination_page_2(self, client, registered_user, seeded_rubric, db):
        email, *_ = registered_user
        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        user = db.query(UserORM).filter(UserORM.email == email).first()

        for i in range(5):
            _seed_result(db, user.id, rubric.id, dims, response_text=f"resp{i}")

        resp = client.get("/api/v1/results?page=2&page_size=2", headers=headers)
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["page"] == 2

    def test_list_filter_min_score(self, client, registered_user, seeded_rubric, db):
        email, *_ = registered_user
        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        user = db.query(UserORM).filter(UserORM.email == email).first()

        _seed_result(db, user.id, rubric.id, dims, composite_score=2.0, response_text="low")
        _seed_result(db, user.id, rubric.id, dims, composite_score=4.5, response_text="high")

        resp = client.get("/api/v1/results?min_score=4.0", headers=headers)
        data = resp.json()
        assert data["total_count"] == 1
        assert data["results"][0]["composite_score"] >= 4.0

    def test_list_filter_max_score(self, client, registered_user, seeded_rubric, db):
        email, *_ = registered_user
        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        user = db.query(UserORM).filter(UserORM.email == email).first()

        _seed_result(db, user.id, rubric.id, dims, composite_score=2.0, response_text="low")
        _seed_result(db, user.id, rubric.id, dims, composite_score=4.5, response_text="high")

        resp = client.get("/api/v1/results?max_score=3.0", headers=headers)
        data = resp.json()
        assert data["total_count"] == 1
        assert data["results"][0]["composite_score"] <= 3.0

    def test_list_filter_rubric_id(self, client, registered_user, seeded_rubric, db):
        email, *_ = registered_user
        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        user = db.query(UserORM).filter(UserORM.email == email).first()

        _seed_result(db, user.id, rubric.id, dims)

        resp = client.get(f"/api/v1/results?rubric_id={rubric.id}", headers=headers)
        assert resp.json()["total_count"] == 1

    def test_list_filter_invalid_rubric_id_returns_422(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.get("/api/v1/results?rubric_id=not-a-uuid", headers=headers)
        assert resp.status_code == 422

    def test_list_isolation_other_user_results_hidden(
        self, client, registered_user, second_user, seeded_rubric, db
    ):
        """FR-023: results of other users are invisible."""
        email1, *_ = registered_user
        _, _, _, h1 = registered_user
        _, _, _, h2 = second_user
        rubric, dims = seeded_rubric

        user1 = db.query(UserORM).filter(UserORM.email == email1).first()
        _seed_result(db, user1.id, rubric.id, dims)

        # user2 should see 0 results
        resp = client.get("/api/v1/results", headers=h2)
        assert resp.json()["total_count"] == 0

    def test_list_unauthenticated_returns_401(self, client):
        resp = client.get("/api/v1/results")
        assert resp.status_code == 401

    def test_list_includes_dimension_scores(self, client, registered_user, seeded_rubric, db):
        email, *_ = registered_user
        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        user = db.query(UserORM).filter(UserORM.email == email).first()
        _seed_result(db, user.id, rubric.id, dims)

        resp = client.get("/api/v1/results", headers=headers)
        result = resp.json()["results"][0]
        assert len(result["dimension_scores"]) == len(dims)


# ── Detail view ───────────────────────────────────────────────────────────────

class TestGetResult:
    def test_get_result_returns_full_detail(self, client, registered_user, seeded_rubric, db):
        email, *_ = registered_user
        _, _, _, headers = registered_user
        rubric, dims = seeded_rubric
        user = db.query(UserORM).filter(UserORM.email == email).first()
        _, result = _seed_result(db, user.id, rubric.id, dims, composite_score=4.0)

        resp = client.get(f"/api/v1/results/{result.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert abs(data["composite_score"] - 4.0) < 1e-6
        assert data["prompt"] == "Test prompt"
        assert len(data["dimension_scores"]) == len(dims)
        for ds in data["dimension_scores"]:
            assert ds["rationale"] == "Test rationale."

    def test_get_result_not_found_returns_404(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.get(f"/api/v1/results/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404

    def test_get_result_invalid_uuid_returns_404(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.get("/api/v1/results/not-a-uuid", headers=headers)
        assert resp.status_code == 404

    def test_get_result_isolation_returns_404_for_other_user(
        self, client, registered_user, second_user, seeded_rubric, db
    ):
        """FR-023: can't fetch another user's result."""
        email1, *_ = registered_user
        _, _, _, h2 = second_user
        rubric, dims = seeded_rubric
        user1 = db.query(UserORM).filter(UserORM.email == email1).first()
        _, result = _seed_result(db, user1.id, rubric.id, dims)

        resp = client.get(f"/api/v1/results/{result.id}", headers=h2)
        assert resp.status_code == 404

    def test_get_result_unauthenticated_returns_401(self, client):
        resp = client.get(f"/api/v1/results/{uuid.uuid4()}")
        assert resp.status_code == 401
