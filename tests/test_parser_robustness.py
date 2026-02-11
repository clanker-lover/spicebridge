"""Tests for parser robustness: empty data, NaN, missing traces, sweep filtering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from spicebridge.parser import (
    _select_output_trace,
    parse_ac,
    parse_dc_op,
    parse_results,
    parse_transient,
    read_ac_at_frequency,
    read_ac_bandwidth,
)


def _mock_raw_read(trace_map, trace_names=None, plot_name="AC Analysis"):
    """Build a mock RawRead returning controlled wave data."""
    mock = MagicMock()
    mock.get_plot_name.return_value = plot_name
    mock.get_trace_names.return_value = trace_names or list(trace_map.keys())

    def get_trace(name):
        if name not in trace_map:
            raise KeyError(f"Trace '{name}' not found")
        trace = MagicMock()
        trace.get_wave.return_value = trace_map[name]
        return trace

    mock.get_trace.side_effect = get_trace
    return mock


# ---------------------------------------------------------------------------
# Empty data
# ---------------------------------------------------------------------------


@patch("spicebridge.parser.RawRead")
def test_parse_ac_empty_arrays(mock_cls):
    mock_cls.return_value = _mock_raw_read(
        {"frequency": np.array([]), "v(out)": np.array([])},
    )
    result = parse_ac("/fake.raw")
    assert "error" in result
    assert "Empty" in result["error"] or "empty" in result["error"].lower()


@patch("spicebridge.parser.RawRead")
def test_parse_transient_empty_arrays(mock_cls):
    mock_cls.return_value = _mock_raw_read(
        {"time": np.array([]), "v(out)": np.array([])},
        plot_name="Transient Analysis",
    )
    result = parse_transient("/fake.raw")
    assert "error" in result


@patch("spicebridge.parser.RawRead")
def test_parse_dc_op_empty_wave(mock_cls):
    mock_cls.return_value = _mock_raw_read(
        {"v(out)": np.array([])},
        plot_name="Operating Point",
    )
    result = parse_dc_op("/fake.raw")
    # Should not crash; the node is skipped and a warning is produced
    assert "error" not in result
    assert result["num_nodes"] == 0
    assert "warnings" in result


@patch("spicebridge.parser.RawRead")
def test_read_ac_at_frequency_empty(mock_cls):
    mock_cls.return_value = _mock_raw_read(
        {"frequency": np.array([]), "v(out)": np.array([])},
    )
    result = read_ac_at_frequency("/fake.raw", 1000.0)
    assert "error" in result


@patch("spicebridge.parser.RawRead")
def test_read_ac_bandwidth_empty(mock_cls):
    mock_cls.return_value = _mock_raw_read(
        {"frequency": np.array([]), "v(out)": np.array([])},
    )
    result = read_ac_bandwidth("/fake.raw", -3.0)
    assert "error" in result


# ---------------------------------------------------------------------------
# NaN data
# ---------------------------------------------------------------------------


@patch("spicebridge.parser.RawRead")
def test_parse_ac_nan_in_data(mock_cls):
    freqs = np.array([1.0, 10.0, 100.0, 1000.0])
    data = np.array([1.0 + 0j, np.nan + 0j, 0.5 + 0j, 0.1 + 0j])
    mock_cls.return_value = _mock_raw_read({"frequency": freqs, "v(out)": data})
    result = parse_ac("/fake.raw")
    assert "error" not in result
    assert "warnings" in result
    assert any("NaN" in w for w in result["warnings"])


@patch("spicebridge.parser.RawRead")
def test_parse_transient_nan_in_voltage(mock_cls):
    time = np.array([0.0, 1e-3, 2e-3, 3e-3])
    voltage = np.array([0.0, np.nan, 1.0, 1.0])
    mock_cls.return_value = _mock_raw_read(
        {"time": time, "v(out)": voltage},
        plot_name="Transient Analysis",
    )
    result = parse_transient("/fake.raw")
    assert "error" not in result
    assert "warnings" in result
    assert any("NaN" in w for w in result["warnings"])


@patch("spicebridge.parser.RawRead")
def test_parse_dc_op_nan_value(mock_cls):
    mock_cls.return_value = _mock_raw_read(
        {"v(out)": np.array([np.nan])},
        plot_name="Operating Point",
    )
    result = parse_dc_op("/fake.raw")
    assert "error" not in result
    assert "warnings" in result
    assert result["nodes"]["v(out)"] == 0.0


# ---------------------------------------------------------------------------
# Single-point data
# ---------------------------------------------------------------------------


@patch("spicebridge.parser.RawRead")
def test_parse_ac_single_point(mock_cls):
    freqs = np.array([1000.0])
    data = np.array([1.0 + 0j])
    mock_cls.return_value = _mock_raw_read({"frequency": freqs, "v(out)": data})
    result = parse_ac("/fake.raw")
    assert "error" not in result
    assert result["num_points"] == 1


@patch("spicebridge.parser.RawRead")
def test_read_ac_at_frequency_single_point(mock_cls):
    freqs = np.array([1000.0])
    data = np.array([1.0 + 0j])
    mock_cls.return_value = _mock_raw_read({"frequency": freqs, "v(out)": data})
    result = read_ac_at_frequency("/fake.raw", 1000.0)
    assert "error" in result
    assert "Insufficient" in result["error"] or ">= 2" in result["error"]


@patch("spicebridge.parser.RawRead")
def test_read_ac_bandwidth_single_point(mock_cls):
    freqs = np.array([1000.0])
    data = np.array([1.0 + 0j])
    mock_cls.return_value = _mock_raw_read({"frequency": freqs, "v(out)": data})
    result = read_ac_bandwidth("/fake.raw", -3.0)
    # Single point should still work (no interpolation needed for bandwidth)
    assert "error" not in result


# ---------------------------------------------------------------------------
# Missing traces
# ---------------------------------------------------------------------------


@patch("spicebridge.parser.RawRead")
def test_parse_ac_missing_frequency_trace(mock_cls):
    mock_cls.return_value = _mock_raw_read(
        {"v(out)": np.array([1.0 + 0j, 0.5 + 0j])},
        trace_names=["v(out)"],
    )
    result = parse_ac("/fake.raw")
    assert "error" in result
    assert "frequency" in result["error"].lower()


@patch("spicebridge.parser.RawRead")
def test_parse_ac_missing_output_trace(mock_cls):
    # frequency exists but output trace doesn't resolve
    raw_mock = _mock_raw_read(
        {"frequency": np.array([1.0, 10.0])},
        trace_names=["frequency"],
    )
    mock_cls.return_value = raw_mock
    result = parse_ac("/fake.raw")
    assert "error" in result


@patch("spicebridge.parser.RawRead")
def test_parse_transient_missing_time_trace(mock_cls):
    mock_cls.return_value = _mock_raw_read(
        {"v(out)": np.array([0.0, 1.0])},
        trace_names=["v(out)"],
        plot_name="Transient Analysis",
    )
    result = parse_transient("/fake.raw")
    assert "error" in result
    assert "time" in result["error"].lower()


@patch("spicebridge.parser.RawRead")
def test_parse_dc_op_missing_trace(mock_cls):
    raw_mock = MagicMock()
    raw_mock.get_trace_names.return_value = ["v(out)", "v(bad)"]

    def get_trace(name):
        if name == "v(bad)":
            raise RuntimeError("corrupt trace")
        trace = MagicMock()
        trace.get_wave.return_value = np.array([1.5])
        return trace

    raw_mock.get_trace.side_effect = get_trace
    mock_cls.return_value = raw_mock
    result = parse_dc_op("/fake.raw")
    assert "error" not in result
    assert "v(out)" in result["nodes"]
    assert "v(bad)" not in result["nodes"]
    assert "warnings" in result


# ---------------------------------------------------------------------------
# Sweep variable filtering
# ---------------------------------------------------------------------------


def test_select_output_trace_filters_frequency():
    result = _select_output_trace(["frequency", "v(out)"])
    assert result == "v(out)"


def test_select_output_trace_only_sweep_vars():
    with pytest.raises(ValueError, match="only sweep variables"):
        _select_output_trace(["frequency", "time"])


def test_select_output_trace_empty_list():
    with pytest.raises(ValueError, match="No traces available"):
        _select_output_trace([])


# ---------------------------------------------------------------------------
# File-level errors
# ---------------------------------------------------------------------------


@patch("spicebridge.parser.RawRead")
def test_parse_results_invalid_file(mock_cls):
    mock_cls.side_effect = FileNotFoundError("no such file")
    result = parse_results("/nonexistent.raw")
    assert "error" in result


@patch("spicebridge.parser.detect_analysis_type")
def test_parse_results_unknown_analysis(mock_detect):
    mock_detect.return_value = "Something Weird"
    result = parse_results("/fake.raw")
    assert "error" in result
    assert "Unknown" in result["error"]
