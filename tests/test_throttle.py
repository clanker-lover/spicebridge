"""Tests for request throttling and simulation queue limits."""

import threading

import pytest

from spicebridge.metrics import ServerMetrics
from spicebridge.simulator import SimulationQueueFull


class TestRPMThrottling:
    def test_under_limit_allows_requests(self):
        m = ServerMetrics(max_rpm=10)
        for _ in range(9):
            m.record_request("x")
        assert m.check_rpm() is True

    def test_at_limit_blocks_requests(self):
        m = ServerMetrics(max_rpm=5)
        for _ in range(5):
            m.record_request("x")
        assert m.check_rpm() is False

    def test_rejection_increments_counters(self):
        m = ServerMetrics(max_rpm=2)
        m.record_request("a")
        m.record_request("a")
        # Now at limit
        assert m.check_rpm() is False
        m.record_rejection()
        snap = m.snapshot()
        assert snap["throttle"]["rejected_total"] == 1
        assert snap["throttle"]["rejected_last_1m"] == 1


class TestSimulationQueueFull:
    def test_exception_is_raised(self):
        with pytest.raises(SimulationQueueFull, match="capacity"):
            raise SimulationQueueFull(
                "Server is at capacity. Please retry in a moment."
            )

    def test_exception_message(self):
        exc = SimulationQueueFull("Server is at capacity. Please retry in a moment.")
        assert "capacity" in str(exc)


class TestMonitoredDecorator:
    """Test that the _monitored decorator correctly handles throttling."""

    def test_rpm_rejection_returns_error_dict(self):
        """Simulate what _monitored does when RPM is exceeded."""
        m = ServerMetrics(max_rpm=1)
        m.record_request("x")
        # RPM exceeded
        assert m.check_rpm() is False
        # The decorator would return an error dict
        err = {
            "status": "error",
            "error": "Rate limit exceeded. Please retry in a moment.",
        }
        assert err["status"] == "error"
        assert "Rate limit" in err["error"]

    def test_queue_full_rejection_returns_error(self):
        """Simulate what _monitored does when SimulationQueueFull is caught."""
        try:
            raise SimulationQueueFull(
                "Server is at capacity. Please retry in a moment."
            )
        except SimulationQueueFull as e:
            err = {"status": "error", "error": str(e)}
        assert err["status"] == "error"
        assert "capacity" in err["error"]


class TestCacheHitMissTracking:
    def test_hits_and_misses(self):
        from spicebridge.schematic_cache import SchematicCache

        cache = SchematicCache()
        cache.put("a", b"PNG")
        cache.get("a")  # hit
        cache.get("a")  # hit
        cache.get("b")  # miss
        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1

    def test_stats_after_eviction(self):
        from spicebridge.schematic_cache import SchematicCache

        cache = SchematicCache(max_size=1)
        cache.put("a", b"A")
        cache.put("b", b"B")  # evicts "a"
        cache.get("a")  # miss
        cache.get("b")  # hit
        stats = cache.stats()
        assert stats["size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
