"""Tests for API key authentication middleware."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from spicebridge.auth import ApiKeyMiddleware

API_KEY = "test-secret-key-12345"


def _homepage(request):
    return JSONResponse({"status": "ok"})


def _make_app(api_key: str = API_KEY) -> ApiKeyMiddleware:
    """Create a simple Starlette app wrapped with ApiKeyMiddleware."""
    inner = Starlette(routes=[Route("/", _homepage), Route("/mcp", _homepage)])
    return ApiKeyMiddleware(inner, api_key)


@pytest.fixture
def client():
    app = _make_app()
    return TestClient(app, raise_server_exceptions=False)


class TestApiKeyMiddleware:
    def test_missing_auth_returns_401(self, client):
        resp = client.get("/")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    def test_wrong_key_returns_403(self, client):
        resp = client.get("/", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 403

    def test_correct_key_returns_200(self, client):
        resp = client.get("/", headers={"Authorization": f"Bearer {API_KEY}"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_malformed_header_basic_returns_401(self, client):
        resp = client.get("/", headers={"Authorization": "Basic abc123"})
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers

    def test_empty_bearer_value_returns_403(self, client):
        resp = client.get("/", headers={"Authorization": "Bearer "})
        assert resp.status_code == 403

    def test_correct_key_on_mcp_path(self, client):
        resp = client.get("/mcp", headers={"Authorization": f"Bearer {API_KEY}"})
        assert resp.status_code == 200
