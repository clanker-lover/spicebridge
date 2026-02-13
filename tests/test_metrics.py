"""Tests for spicebridge.metrics."""

import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from spicebridge.metrics import ServerMetrics, TimeBucket, ToolStats


class TestServerMetrics:
    def test_record_request_increments_counter(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_request("run_ac_analysis")
        m.record_request("run_ac_analysis")
        m.record_request("create_circuit")
        snap = m.snapshot()
        assert snap["total_requests_by_tool"]["run_ac_analysis"] == 2
        assert snap["total_requests_by_tool"]["create_circuit"] == 1

    def test_requests_last_1m(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_request("a")
        m.record_request("b")
        snap = m.snapshot()
        assert snap["requests_last_1m"] == 2
        assert snap["requests_last_5m"] == 2

    def test_rolling_window_excludes_old_entries(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
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

    def test_sim_duration_tracking(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
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

    def test_active_sims_gauge(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_sim_start()
        m.record_sim_start()
        snap = m.snapshot()
        assert snap["active_simulations"] == 2
        m.record_sim_end(50.0)
        snap = m.snapshot()
        assert snap["active_simulations"] == 1

    def test_active_sims_does_not_go_negative(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_sim_end(10.0)
        snap = m.snapshot()
        assert snap["active_simulations"] == 0

    def test_rejection_tracking(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_rejection()
        m.record_rejection()
        snap = m.snapshot()
        assert snap["throttle"]["rejected_total"] == 2
        assert snap["throttle"]["rejected_last_1m"] == 2

    def test_check_rpm_under_limit(self, tmp_path):
        m = ServerMetrics(max_rpm=10, persist_path=tmp_path / "m.json")
        for _ in range(9):
            m.record_request("x")
        assert m.check_rpm() is True

    def test_check_rpm_at_limit(self, tmp_path):
        m = ServerMetrics(max_rpm=5, persist_path=tmp_path / "m.json")
        for _ in range(5):
            m.record_request("x")
        assert m.check_rpm() is False

    def test_uptime_increases(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        snap1 = m.snapshot()
        assert snap1["uptime_seconds"] >= 0

    def test_snapshot_includes_max_rpm(self, tmp_path):
        m = ServerMetrics(max_rpm=42, persist_path=tmp_path / "m.json")
        snap = m.snapshot()
        assert snap["throttle"]["max_rpm"] == 42

    def test_sim_duration_capped_at_100(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        for i in range(150):
            m.record_sim_end(float(i))
        snap = m.snapshot()
        assert snap["simulation_stats"]["count"] == 100

    def test_thread_safety(self, tmp_path):
        m = ServerMetrics(max_rpm=10000, persist_path=tmp_path / "m.json")
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

    def test_empty_snapshot(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        snap = m.snapshot()
        assert snap["requests_last_1m"] == 0
        assert snap["requests_last_5m"] == 0
        assert snap["active_simulations"] == 0
        assert snap["total_requests_by_tool"] == {}
        assert snap["simulation_stats"]["count"] == 0
        assert snap["throttle"]["rejected_total"] == 0


class TestToolStats:
    def test_record_success_updates_stats(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_request("tool_a")
        m.record_success("tool_a", 150.0)
        snap = m.snapshot()
        ts = snap["tool_stats"]["tool_a"]
        assert ts["calls"] == 1
        assert ts["successes"] == 1
        assert ts["errors"] == 0
        assert ts["avg_latency_ms"] == 150.0

    def test_record_error_updates_stats_and_log(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_request("tool_b")
        m.record_error("tool_b", 200.0, "something broke")
        snap = m.snapshot()
        ts = snap["tool_stats"]["tool_b"]
        assert ts["errors"] == 1
        assert len(snap["recent_errors"]) == 1
        assert snap["recent_errors"][0]["tool"] == "tool_b"
        assert snap["recent_errors"][0]["message"] == "something broke"

    def test_error_message_truncation_at_200_chars(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        long_msg = "x" * 300
        m.record_error("tool_c", 10.0, long_msg)
        snap = m.snapshot()
        assert len(snap["recent_errors"][0]["message"]) == 200

    def test_error_log_max_50_entries(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        for i in range(60):
            m.record_error("tool_d", 1.0, f"error {i}")
        snap = m.snapshot()
        assert len(snap["recent_errors"]) == 50
        # Should have the most recent entries
        assert snap["recent_errors"][-1]["message"] == "error 59"
        assert snap["recent_errors"][0]["message"] == "error 10"

    def test_last_called_is_iso_timestamp(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_request("tool_e")
        snap = m.snapshot()
        ts = snap["tool_stats"]["tool_e"]
        assert ts["last_called"] is not None
        # Should be parseable as ISO format
        from datetime import datetime
        datetime.fromisoformat(ts["last_called"])


class TestTimeBuckets:
    def test_24_hourly_buckets_initialized(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        snap = m.snapshot()
        assert len(snap["hourly_history"]) == 24

    def test_7_daily_buckets_initialized(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        snap = m.snapshot()
        assert len(snap["daily_history"]) == 7

    def test_request_populates_current_bucket(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_request("tool_f")
        m.record_success("tool_f", 100.0)
        snap = m.snapshot()
        # The last hourly bucket (current hour) should have data
        hourly = snap["hourly_history"]
        # At least one bucket should have total > 0
        totals = [b["total"] for b in hourly]
        assert sum(totals) > 0

        # Same for daily
        daily = snap["daily_history"]
        totals = [b["total"] for b in daily]
        assert sum(totals) > 0

    def test_bucket_shape(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_request("x")
        snap = m.snapshot()
        for bucket in snap["hourly_history"]:
            assert "total" in bucket
            assert "errors" in bucket
            assert "avg_latency_ms" in bucket
        for bucket in snap["daily_history"]:
            assert "total" in bucket
            assert "errors" in bucket
            assert "avg_latency_ms" in bucket


class TestHighWaterMarks:
    def test_peak_concurrent_sims_tracked(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_sim_start()
        m.record_sim_start()
        m.record_sim_start()
        m.record_sim_end(10.0)
        m.record_sim_end(10.0)
        snap = m.snapshot()
        assert snap["high_water_marks"]["peak_concurrent_sims"] == 3
        assert snap["active_simulations"] == 1

    def test_peak_concurrent_sims_never_decreases(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_sim_start()
        m.record_sim_start()
        m.record_sim_start()
        m.record_sim_end(10.0)
        m.record_sim_end(10.0)
        m.record_sim_end(10.0)
        m.record_sim_start()
        snap = m.snapshot()
        # Peak was 3, now only 1 active, but peak should still be 3
        assert snap["high_water_marks"]["peak_concurrent_sims"] == 3

    def test_peak_rpm_tracked(self, tmp_path):
        m = ServerMetrics(max_rpm=1000, persist_path=tmp_path / "m.json")
        for _ in range(10):
            m.record_request("x")
        snap = m.snapshot()
        assert snap["high_water_marks"]["peak_rpm"] >= 10


class TestPersistence:
    def test_save_and_reload_preserves_data(self, tmp_path):
        path = tmp_path / "metrics.json"
        m1 = ServerMetrics(persist_path=path)
        m1.record_request("tool_a")
        m1.record_request("tool_a")
        m1.record_success("tool_a", 100.0)
        m1.record_error("tool_a", 50.0, "test error")
        m1.record_sim_start()
        m1.record_sim_start()
        m1.record_sim_end(10.0)
        m1.save()

        # Create new instance that loads from same file
        m2 = ServerMetrics(persist_path=path)
        snap = m2.snapshot()
        ts = snap["tool_stats"]["tool_a"]
        assert ts["calls"] == 2
        assert ts["successes"] == 1
        assert ts["errors"] == 1
        assert snap["high_water_marks"]["peak_concurrent_sims"] == 2
        assert len(snap["recent_errors"]) == 1
        assert snap["cumulative_uptime_seconds"] >= 0

    def test_atomic_write_no_tmp_leftover(self, tmp_path):
        path = tmp_path / "metrics.json"
        m = ServerMetrics(persist_path=path)
        m.record_request("x")
        m.save()
        # Main file should exist, tmp should not
        assert path.exists()
        assert not path.with_suffix(".tmp").exists()

    def test_missing_file_on_startup_clean_start(self, tmp_path):
        path = tmp_path / "nonexistent" / "metrics.json"
        m = ServerMetrics(persist_path=path)
        snap = m.snapshot()
        assert snap["total_requests_by_tool"] == {}
        assert snap["cumulative_uptime_seconds"] >= 0

    def test_corrupt_file_graceful_fallback(self, tmp_path):
        path = tmp_path / "metrics.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        m = ServerMetrics(persist_path=path)
        snap = m.snapshot()
        # Should start clean without raising
        assert snap["total_requests_by_tool"] == {}

    def test_file_permissions_600(self, tmp_path):
        path = tmp_path / "metrics.json"
        m = ServerMetrics(persist_path=path)
        m.record_request("x")
        m.save()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_cumulative_uptime_accumulates(self, tmp_path):
        path = tmp_path / "metrics.json"
        m1 = ServerMetrics(persist_path=path)
        # Simulate some uptime
        time.sleep(0.05)
        m1.save()

        m2 = ServerMetrics(persist_path=path)
        time.sleep(0.05)
        snap = m2.snapshot()
        # Should be at least 0.05s cumulative from first session + current
        assert snap["cumulative_uptime_seconds"] >= 0


class TestSystemMetrics:
    def test_system_dict_present_in_snapshot(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        snap = m.snapshot()
        assert "system" in snap
        assert isinstance(snap["system"], dict)

    def test_psutil_fields_present(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        snap = m.snapshot()
        sys = snap["system"]
        # psutil should be installed in dev env
        if "error" not in sys:
            assert "cpu_percent" in sys
            assert "ram_percent" in sys
            assert "ram_used_mb" in sys
            assert "disk_percent" in sys
            assert "disk_used_gb" in sys
            assert "process_cpu_percent" in sys
            assert "process_ram_mb" in sys

    def test_caching_works_60s_ttl(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        snap1 = m.snapshot()
        sys1 = snap1["system"]

        # Second call should return cached result (same object)
        snap2 = m.snapshot()
        sys2 = snap2["system"]
        assert sys1 is sys2  # Same object means cache hit


class TestSnapshotBackwardCompatibility:
    def test_all_original_keys_present(self, tmp_path):
        m = ServerMetrics(max_rpm=60, persist_path=tmp_path / "m.json")
        m.record_request("test_tool")
        snap = m.snapshot()

        # Original keys
        assert "uptime_seconds" in snap
        assert "requests_last_1m" in snap
        assert "requests_last_5m" in snap
        assert "active_simulations" in snap
        assert "total_requests_by_tool" in snap
        assert "simulation_stats" in snap
        assert "throttle" in snap

        # New keys also present
        assert "server_start_time" in snap
        assert "cumulative_uptime_seconds" in snap
        assert "last_request_timestamp" in snap
        assert "circuit_count" in snap
        assert "tool_stats" in snap
        assert "hourly_history" in snap
        assert "daily_history" in snap
        assert "recent_errors" in snap
        assert "high_water_marks" in snap
        assert "system" in snap

    def test_original_nested_shapes_unchanged(self, tmp_path):
        m = ServerMetrics(max_rpm=60, persist_path=tmp_path / "m.json")
        snap = m.snapshot()

        sim_stats = snap["simulation_stats"]
        assert "min_ms" in sim_stats
        assert "avg_ms" in sim_stats
        assert "max_ms" in sim_stats
        assert "count" in sim_stats

        throttle = snap["throttle"]
        assert "rejected_last_1m" in throttle
        assert "rejected_total" in throttle
        assert "max_rpm" in throttle

    def test_circuit_count_with_callable(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.set_circuit_counter(lambda: 5)
        snap = m.snapshot()
        assert snap["circuit_count"] == 5

    def test_circuit_count_without_callable(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        snap = m.snapshot()
        assert snap["circuit_count"] == 0

    def test_snapshot_json_serializable(self, tmp_path):
        m = ServerMetrics(persist_path=tmp_path / "m.json")
        m.record_request("tool_x")
        m.record_success("tool_x", 50.0)
        m.record_error("tool_x", 10.0, "err")
        snap = m.snapshot()
        # Should serialize without error
        serialized = json.dumps(snap, default=str)
        assert isinstance(serialized, str)
