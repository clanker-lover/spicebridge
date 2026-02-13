"""Tests for spicebridge.metrics."""

import threading
import time

from spicebridge.metrics import ServerMetrics


class TestServerMetrics:
    def test_record_request_increments_counter(self):
        m = ServerMetrics()
        m.record_request("run_ac_analysis")
        m.record_request("run_ac_analysis")
        m.record_request("create_circuit")
        snap = m.snapshot()
        assert snap["total_requests_by_tool"]["run_ac_analysis"] == 2
        assert snap["total_requests_by_tool"]["create_circuit"] == 1

    def test_requests_last_1m(self):
        m = ServerMetrics()
        m.record_request("a")
        m.record_request("b")
        snap = m.snapshot()
        assert snap["requests_last_1m"] == 2
        assert snap["requests_last_5m"] == 2

    def test_rolling_window_excludes_old_entries(self):
        m = ServerMetrics()
        # Manually insert an old timestamp
        old_time = time.monotonic() - 120  # 2 minutes ago
        with m._lock:
            m._request_times.append(old_time)
            m._tool_counts["old"] = 1
        m.record_request("new")
        snap = m.snapshot()
        # Old entry should be pruned from 1m window
        assert snap["requests_last_1m"] == 1
        # Old entry also older than 5m? No, 2 min < 5 min, so it's in the 5m window
        assert snap["requests_last_5m"] == 2

    def test_sim_duration_tracking(self):
        m = ServerMetrics()
        m.record_sim_start()
        m.record_sim_end(100.0)
        m.record_sim_start()
        m.record_sim_end(300.0)
        m.record_sim_start()
        m.record_sim_end(200.0)
        snap = m.snapshot()
        assert snap["simulation_stats"]["min_ms"] == 100
        assert snap["simulation_stats"]["max_ms"] == 300
        assert snap["simulation_stats"]["avg_ms"] == 200
        assert snap["simulation_stats"]["count"] == 3

    def test_active_sims_gauge(self):
        m = ServerMetrics()
        m.record_sim_start()
        m.record_sim_start()
        snap = m.snapshot()
        assert snap["active_simulations"] == 2
        m.record_sim_end(50.0)
        snap = m.snapshot()
        assert snap["active_simulations"] == 1

    def test_active_sims_does_not_go_negative(self):
        m = ServerMetrics()
        m.record_sim_end(10.0)
        snap = m.snapshot()
        assert snap["active_simulations"] == 0

    def test_rejection_tracking(self):
        m = ServerMetrics()
        m.record_rejection()
        m.record_rejection()
        snap = m.snapshot()
        assert snap["throttle"]["rejected_total"] == 2
        assert snap["throttle"]["rejected_last_1m"] == 2

    def test_check_rpm_under_limit(self):
        m = ServerMetrics(max_rpm=10)
        for _ in range(9):
            m.record_request("x")
        assert m.check_rpm() is True

    def test_check_rpm_at_limit(self):
        m = ServerMetrics(max_rpm=5)
        for _ in range(5):
            m.record_request("x")
        assert m.check_rpm() is False

    def test_uptime_increases(self):
        m = ServerMetrics()
        snap1 = m.snapshot()
        assert snap1["uptime_seconds"] >= 0

    def test_snapshot_includes_max_rpm(self):
        m = ServerMetrics(max_rpm=42)
        snap = m.snapshot()
        assert snap["throttle"]["max_rpm"] == 42

    def test_sim_duration_capped_at_100(self):
        m = ServerMetrics()
        for i in range(150):
            m.record_sim_end(float(i))
        snap = m.snapshot()
        assert snap["simulation_stats"]["count"] == 100

    def test_thread_safety(self):
        m = ServerMetrics(max_rpm=10000)
        errors = []

        def record_many():
            try:
                for _ in range(100):
                    m.record_request("threaded")
                    m.record_sim_start()
                    m.record_sim_end(1.0)
                    m.record_rejection()
                    m.snapshot()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        snap = m.snapshot()
        assert snap["total_requests_by_tool"]["threaded"] == 1000
        assert snap["throttle"]["rejected_total"] == 1000

    def test_empty_snapshot(self):
        m = ServerMetrics()
        snap = m.snapshot()
        assert snap["requests_last_1m"] == 0
        assert snap["requests_last_5m"] == 0
        assert snap["active_simulations"] == 0
        assert snap["total_requests_by_tool"] == {}
        assert snap["simulation_stats"]["count"] == 0
        assert snap["throttle"]["rejected_total"] == 0
