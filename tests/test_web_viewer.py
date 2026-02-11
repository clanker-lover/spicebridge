"""Async tests for the web viewer API using pytest-aiohttp."""

import pytest

from spicebridge.circuit_manager import CircuitManager
from spicebridge.web_viewer import _ViewerServer

RC_LOWPASS = """\
* RC Low-Pass Filter
V1 in 0 AC 1
R1 in out 1k
C1 out 0 100n
.end
"""


@pytest.fixture
def manager():
    return CircuitManager()


@pytest.fixture
def viewer_app(manager):
    """Create an aiohttp app from the viewer server for testing."""
    server = _ViewerServer(manager, "127.0.0.1", 0)
    return server._build_app()


@pytest.fixture
async def cli(aiohttp_client, viewer_app):
    """Create an aiohttp test client."""
    return await aiohttp_client(viewer_app)


class TestIndexPage:
    @pytest.mark.asyncio
    async def test_get_index_returns_html(self, cli):
        resp = await cli.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "SPICEBridge" in text
        assert resp.content_type == "text/html"


class TestCircuitsAPI:
    @pytest.mark.asyncio
    async def test_list_circuits_empty(self, cli):
        resp = await cli.get("/api/circuits")
        assert resp.status == 200
        data = await resp.json()
        assert data == []

    @pytest.mark.asyncio
    async def test_list_circuits_after_create(self, cli, manager):
        cid = manager.create(RC_LOWPASS)
        resp = await cli.get("/api/circuits")
        assert resp.status == 200
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["circuit_id"] == cid
        assert data[0]["has_results"] is False

    @pytest.mark.asyncio
    async def test_get_circuit_info(self, cli, manager):
        cid = manager.create(RC_LOWPASS)
        resp = await cli.get(f"/api/circuit/{cid}")
        assert resp.status == 200
        data = await resp.json()
        assert data["circuit_id"] == cid
        assert len(data["components"]) == 3  # V1, R1, C1
        assert data["has_results"] is False

    @pytest.mark.asyncio
    async def test_get_circuit_not_found(self, cli):
        resp = await cli.get("/api/circuit/nonexistent")
        assert resp.status == 404


class TestSvgAPI:
    @pytest.mark.asyncio
    async def test_get_svg(self, cli, manager):
        cid = manager.create(RC_LOWPASS)
        resp = await cli.get(f"/api/circuit/{cid}/svg")
        assert resp.status == 200
        assert resp.content_type == "image/svg+xml"
        text = await resp.text()
        assert "<svg" in text


class TestResultsAPI:
    @pytest.mark.asyncio
    async def test_results_null_before_sim(self, cli, manager):
        cid = manager.create(RC_LOWPASS)
        resp = await cli.get(f"/api/circuit/{cid}/results")
        assert resp.status == 200
        data = await resp.json()
        assert data["results"] is None

    @pytest.mark.asyncio
    async def test_results_after_update(self, cli, manager):
        cid = manager.create(RC_LOWPASS)
        manager.update_results(cid, {"analysis_type": "test", "nodes": {"out": 0.5}})
        resp = await cli.get(f"/api/circuit/{cid}/results")
        data = await resp.json()
        assert data["results"]["nodes"]["out"] == 0.5
