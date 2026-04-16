"""Integration tests for POST /auth/register, /auth/login, /auth/refresh."""

import pytest


# ── Register ──────────────────────────────────────────────────────────────────

class TestRegister:
    def test_register_returns_201_with_user_data(self, client):
        resp = client.post("/auth/register", json={"email": "new@test.com", "password": "pw123"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "new@test.com"
        assert "id" in data
        assert "created_at" in data

    def test_register_duplicate_email_returns_409(self, client):
        payload = {"email": "dup@test.com", "password": "pw123"}
        client.post("/auth/register", json=payload)
        resp = client.post("/auth/register", json=payload)
        assert resp.status_code == 409

    def test_register_invalid_email_returns_422(self, client):
        resp = client.post("/auth/register", json={"email": "not-an-email", "password": "pw"})
        assert resp.status_code == 422

    def test_register_missing_password_returns_422(self, client):
        resp = client.post("/auth/register", json={"email": "x@test.com"})
        assert resp.status_code == 422

    def test_register_strips_whitespace_from_email(self, client):
        resp = client.post("/auth/register", json={"email": " trim@test.com ", "password": "pw"})
        assert resp.status_code == 201
        assert resp.json()["email"] == "trim@test.com"


# ── Login ─────────────────────────────────────────────────────────────────────

class TestLogin:
    def test_login_returns_access_and_refresh_tokens(self, client):
        client.post("/auth/register", json={"email": "u@test.com", "password": "secret"})
        resp = client.post("/auth/login", data={"username": "u@test.com", "password": "secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password_returns_401(self, client):
        client.post("/auth/register", json={"email": "u@test.com", "password": "secret"})
        resp = client.post("/auth/login", data={"username": "u@test.com", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_unknown_email_returns_401(self, client):
        resp = client.post("/auth/login", data={"username": "ghost@test.com", "password": "pw"})
        assert resp.status_code == 401

    def test_login_error_message_is_generic(self, client):
        """NFR-023: single generic detail, not separate field-level errors."""
        resp = client.post("/auth/login", data={"username": "ghost@test.com", "password": "pw"})
        # Must be a single error string, not individual field reports
        data = resp.json()
        assert isinstance(data["detail"], str)


# ── Refresh ───────────────────────────────────────────────────────────────────

class TestRefresh:
    def _get_tokens(self, client):
        client.post("/auth/register", json={"email": "r@test.com", "password": "pw"})
        resp = client.post("/auth/login", data={"username": "r@test.com", "password": "pw"})
        return resp.json()

    def test_refresh_returns_new_token_pair(self, client):
        tokens = self._get_tokens(client)
        resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_refresh_with_access_token_returns_401(self, client):
        tokens = self._get_tokens(client)
        resp = client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
        assert resp.status_code == 401

    def test_refresh_with_garbage_token_returns_401(self, client):
        resp = client.post("/auth/refresh", json={"refresh_token": "not.a.token"})
        assert resp.status_code == 401

    def test_new_access_token_works_on_protected_endpoint(self, client, seeded_rubric):
        tokens = self._get_tokens(client)
        refresh_resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        new_access = refresh_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {new_access}"}
        resp = client.get("/api/v1/rubrics", headers=headers)
        assert resp.status_code == 200


# ── Protected endpoint guard ──────────────────────────────────────────────────

class TestProtectedEndpoint:
    def test_no_token_returns_401(self, client):
        resp = client.get("/api/v1/rubrics")
        assert resp.status_code == 401

    def test_refresh_token_rejected_on_protected_endpoint(self, client):
        client.post("/auth/register", json={"email": "g@test.com", "password": "pw"})
        tokens = client.post(
            "/auth/login", data={"username": "g@test.com", "password": "pw"}
        ).json()
        headers = {"Authorization": f"Bearer {tokens['refresh_token']}"}
        resp = client.get("/api/v1/rubrics", headers=headers)
        assert resp.status_code == 401

    def test_malformed_bearer_returns_401(self, client):
        resp = client.get("/api/v1/rubrics", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
