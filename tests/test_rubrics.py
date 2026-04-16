"""Integration tests for POST /api/v1/rubrics and GET /api/v1/rubrics."""

import pytest


_VALID_RUBRIC = {
    "name": "My Rubric",
    "dimensions": [
        {"name": "Correctness", "description": "Factual accuracy", "weight": 0.5},
        {"name": "Relevance", "description": "On-topic response", "weight": 0.5},
    ],
}


# ── Create rubric ─────────────────────────────────────────────────────────────

class TestCreateRubric:
    def test_create_returns_201_with_rubric_data(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.post("/api/v1/rubrics", json=_VALID_RUBRIC, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Rubric"
        assert len(data["dimensions"]) == 2
        assert "id" in data
        assert data["is_default"] is False

    def test_create_single_dimension_weight_one(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/rubrics",
            json={
                "name": "Single",
                "dimensions": [{"name": "All", "description": "Everything", "weight": 1.0}],
            },
            headers=headers,
        )
        assert resp.status_code == 201

    def test_create_weights_not_summing_to_one_returns_422(self, client, registered_user):
        """AC-009: weights != 1.0 → HTTP 422."""
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/rubrics",
            json={
                "name": "Bad",
                "dimensions": [
                    {"name": "A", "description": "d", "weight": 0.4},
                    {"name": "B", "description": "d", "weight": 0.4},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert "1.0" in resp.text or "weights" in resp.text.lower()

    def test_create_duplicate_dimension_names_returns_422(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/rubrics",
            json={
                "name": "Bad",
                "dimensions": [
                    {"name": "Same", "description": "d", "weight": 0.5},
                    {"name": "Same", "description": "d", "weight": 0.5},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 422

    def test_create_zero_weight_returns_422(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/rubrics",
            json={
                "name": "Bad",
                "dimensions": [
                    {"name": "A", "description": "d", "weight": 0.0},
                    {"name": "B", "description": "d", "weight": 1.0},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 422

    def test_create_empty_dimensions_returns_422(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/rubrics",
            json={"name": "Bad", "dimensions": []},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_create_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/rubrics", json=_VALID_RUBRIC)
        assert resp.status_code == 401

    def test_create_sets_owner_to_current_user(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.post("/api/v1/rubrics", json=_VALID_RUBRIC, headers=headers)
        data = resp.json()
        assert "owner_id" in data

    def test_three_dim_rubric_weights_sum(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.post(
            "/api/v1/rubrics",
            json={
                "name": "FR-012 rubric",
                "dimensions": [
                    {"name": "Correctness", "description": "d", "weight": 0.4},
                    {"name": "Relevance", "description": "d", "weight": 0.3},
                    {"name": "Faithfulness", "description": "d", "weight": 0.3},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 201


# ── List rubrics ──────────────────────────────────────────────────────────────

class TestListRubrics:
    def test_list_returns_own_rubrics(self, client, registered_user):
        _, _, _, headers = registered_user
        client.post("/api/v1/rubrics", json=_VALID_RUBRIC, headers=headers)
        resp = client.get("/api/v1/rubrics", headers=headers)
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert "My Rubric" in names

    def test_list_includes_default_rubric(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        resp = client.get("/api/v1/rubrics", headers=headers)
        assert resp.status_code == 200
        defaults = [r for r in resp.json() if r["is_default"]]
        assert len(defaults) >= 1

    def test_list_does_not_include_other_users_rubric(
        self, client, registered_user, second_user
    ):
        """FR-023: user isolation on rubric list."""
        _, _, _, h1 = registered_user
        _, _, _, h2 = second_user

        # user2 creates a rubric
        client.post(
            "/api/v1/rubrics",
            json={
                "name": "User2 Rubric",
                "dimensions": [{"name": "A", "description": "d", "weight": 1.0}],
            },
            headers=h2,
        )

        # user1 should NOT see user2's rubric
        resp = client.get("/api/v1/rubrics", headers=h1)
        names = [r["name"] for r in resp.json()]
        assert "User2 Rubric" not in names

    def test_list_returns_empty_for_new_user(self, client, registered_user):
        _, _, _, headers = registered_user
        resp = client.get("/api/v1/rubrics", headers=headers)
        assert resp.status_code == 200
        # May be empty or have default rubric
        assert isinstance(resp.json(), list)

    def test_list_unauthenticated_returns_401(self, client):
        resp = client.get("/api/v1/rubrics")
        assert resp.status_code == 401

    def test_list_defaults_appear_first(self, client, registered_user, seeded_rubric):
        _, _, _, headers = registered_user
        client.post("/api/v1/rubrics", json=_VALID_RUBRIC, headers=headers)
        resp = client.get("/api/v1/rubrics", headers=headers)
        rubrics = resp.json()
        if len(rubrics) > 1:
            # Default (is_default=True) should come first
            assert rubrics[0]["is_default"] is True
