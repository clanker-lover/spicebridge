"""Tests for /health endpoint, auth exemption, and token protection."""

import json
import os

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from spicebridge.auth import ApiKeyMiddleware

API_KEY = "test-key-health"
HEALTH_TOKEN = "supersecrettoken1234"


# ---------------------------------------------------------------------------
# Minimal handler that mirrors the real health endpoint's token logic
# ---------------------------------------------------------------------------

def _mcp_handler(request):
    return JSONResponse({"status": "ok"})


def _health_handler(request):
    """Mirrors server.py token-gated logic for middleware-level tests."""
    import hmac

    expected = os.environ.get("SPICEBRIDGE_HEALTH_TOKEN", "")
    if not expected:
        return Response(status_code=404)

    provided = request.query_params.get("token", "")
    if not provided or not hmac.compare_digest(provided, expected):
        return Response(status_code=404)

    return Response(
        content=json.dumps({"status": "ok", "uptime_seconds": 42}),
        status_code=200,
        media_type="application/json",
    )


def _make_app():
    inner = Starlette(
        routes=[
            Route("/mcp", _mcp_handler),
            Route("/health", _health_handler),
        ]
    )
    return ApiKeyMiddleware(inner, API_KEY)


@pytest.fixture
def client_with_token(monkeypatch):
    monkeypatch.setenv("SPICEBRIDGE_HEALTH_TOKEN", HEALTH_TOKEN)
    return TestClient(_make_app(), raise_server_exceptions=False)


@pytest.fixture
def client_no_token(monkeypatch):
    monkeypatch.delenv("SPICEBRIDGE_HEALTH_TOKEN", raising=False)
    return TestClient(_make_app(), raise_server_exceptions=False)


@pytest.fixture
def client_empty_token(monkeypatch):
    monkeypatch.setenv("SPICEBRIDGE_HEALTH_TOKEN", "")
    return TestClient(_make_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Auth exemption (health skips API-key middleware regardless of token)
# ---------------------------------------------------------------------------

class TestHealthAuthExemption:
    def test_health_exempt_from_auth(self, client_with_token):
        resp = client_with_token.get(f"/health?token={HEALTH_TOKEN}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_mcp_still_requires_auth(self, client_with_token):
        resp = client_with_token.get("/mcp")
        assert resp.status_code == 401

    def test_health_returns_json(self, client_with_token):
        resp = client_with_token.get(f"/health?token={HEALTH_TOKEN}")
        assert resp.headers["content-type"] == "application/json"
        data = resp.json()
        assert "uptime_seconds" in data


# ---------------------------------------------------------------------------
# Token-based access control
# ---------------------------------------------------------------------------

class TestHealthTokenProtection:
    def test_no_token_env_returns_404(self, client_no_token):
        """SPICEBRIDGE_HEALTH_TOKEN unset → always 404."""
        resp = client_no_token.get("/health")
        assert resp.status_code == 404

    def test_empty_token_env_returns_404(self, client_empty_token):
        """SPICEBRIDGE_HEALTH_TOKEN='' → always 404."""
        resp = client_empty_token.get("/health")
        assert resp.status_code == 404

    def test_wrong_token_returns_404(self, client_with_token):
        resp = client_with_token.get("/health?token=wrong-token")
        assert resp.status_code == 404

    def test_missing_query_token_returns_404(self, client_with_token):
        resp = client_with_token.get("/health")
        assert resp.status_code == 404

    def test_correct_token_returns_200(self, client_with_token):
        resp = client_with_token.get(f"/health?token={HEALTH_TOKEN}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data


# ---------------------------------------------------------------------------
# Shape tests (metrics snapshot & cache stats — no token logic involved)
# ---------------------------------------------------------------------------

class TestHealthEndpointShape:
    """Test the actual health endpoint from server.py returns expected keys."""

    def test_snapshot_shape(self):
        from spicebridge.metrics import ServerMetrics

        m = ServerMetrics(max_rpm=60)
        m.record_request("test_tool")
        snap = m.snapshot()

        # Verify all expected top-level keys exist
        assert "uptime_seconds" in snap
        assert "requests_last_1m" in snap
        assert "requests_last_5m" in snap
        assert "active_simulations" in snap
        assert "total_requests_by_tool" in snap
        assert "simulation_stats" in snap
        assert "throttle" in snap

        # Verify nested shapes
        sim_stats = snap["simulation_stats"]
        assert "min_ms" in sim_stats
        assert "avg_ms" in sim_stats
        assert "max_ms" in sim_stats
        assert "count" in sim_stats

        throttle = snap["throttle"]
        assert "rejected_last_1m" in throttle
        assert "rejected_total" in throttle
        assert "max_rpm" in throttle

    def test_cache_stats_shape(self):
        from spicebridge.schematic_cache import SchematicCache

        cache = SchematicCache(max_size=10)
        cache.put("a", b"data")
        cache.get("a")
        cache.get("missing")
        stats = cache.stats()

        assert stats == {"size": 1, "max": 10, "hits": 1, "misses": 1}
