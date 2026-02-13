"""Integration tests for the auto_design MCP tool."""

import base64
import json

from mcp.types import ImageContent, TextContent
from spicebridge.server import auto_design


def _extract_metadata(result):
    """Extract JSON metadata dict from content block list."""
    assert isinstance(result, list)
    assert isinstance(result[0], TextContent)
    return json.loads(result[0].text)


def test_auto_design_rc_lowpass_ac():
    """Happy path: 1kHz RC lowpass, all_specs_passed=True, f_3dB within 10%."""
    result_blocks = auto_design(
        template_id="rc_lowpass_1st",
        specs={"f_3dB_hz": {"target": 1000, "tolerance_pct": 10}},
        sim_type="ac",
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "ok"
    assert result["all_specs_passed"] is True
    assert result["circuit_id"]
    # Verify the measured f_3dB is within 10% of 1000 Hz
    actual = result["comparison"]["results"]["f_3dB_hz"]["actual"]
    assert abs(actual - 1000) / 1000 < 0.10


def test_auto_design_multiple_specs():
    """Multiple AC specs (f_3dB + gain_dc_dB) in one call."""
    result_blocks = auto_design(
        template_id="rc_lowpass_1st",
        specs={
            "f_3dB_hz": {"target": 1000, "tolerance_pct": 10},
            "gain_dc_dB": {"target": 0, "tolerance_pct": 5},
        },
        sim_type="ac",
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "ok"
    assert "f_3dB_hz" in result["comparison"]["results"]
    assert "gain_dc_dB" in result["comparison"]["results"]


def test_auto_design_custom_sim_params():
    """sim_params override defaults."""
    result_blocks = auto_design(
        template_id="rc_lowpass_1st",
        specs={"f_3dB_hz": {"target": 1000, "tolerance_pct": 10}},
        sim_type="ac",
        sim_params={"start_freq": 10, "stop_freq": 100000, "points_per_decade": 50},
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "ok"
    assert result["circuit_id"]


def test_auto_design_voltage_divider_dc():
    """DC path: voltage divider, v(out) target."""
    result_blocks = auto_design(
        template_id="voltage_divider",
        specs={"v(out)": {"target": 5.0, "tolerance_pct": 5}},
        sim_type="dc",
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "ok"
    assert result["all_specs_passed"] is True
    actual = result["comparison"]["results"]["v(out)"]["actual"]
    assert abs(actual - 5.0) / 5.0 < 0.05


def test_auto_design_bad_template_id():
    """Error at load_template, failed_step='load_template'."""
    result_blocks = auto_design(
        template_id="nonexistent_template",
        specs={"f_3dB_hz": {"target": 1000, "tolerance_pct": 5}},
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "error"
    assert result["failed_step"] == "load_template"


def test_auto_design_invalid_sim_type():
    """Error at simulation, partial results include circuit_id."""
    result_blocks = auto_design(
        template_id="rc_lowpass_1st",
        specs={"f_3dB_hz": {"target": 1000, "tolerance_pct": 10}},
        sim_type="unknown_type",
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "error"
    assert result["failed_step"] == "simulation"
    assert result["circuit_id"]  # partial results present


def test_auto_design_spec_fails():
    """status='ok' but all_specs_passed=False (impossible spec)."""
    result_blocks = auto_design(
        template_id="rc_lowpass_1st",
        specs={"f_3dB_hz": {"target": 999999, "tolerance_pct": 0.001}},
        sim_type="ac",
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "ok"
    assert result["all_specs_passed"] is False


def test_auto_design_returns_netlist_preview():
    """Result includes netlist_preview with content."""
    result_blocks = auto_design(
        template_id="rc_lowpass_1st",
        specs={"f_3dB_hz": {"target": 1000, "tolerance_pct": 10}},
        sim_type="ac",
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "ok"
    assert "netlist_preview" in result
    assert len(result["netlist_preview"]) > 0


def test_auto_design_returns_svg_content():
    """Result includes svg_content with valid SVG markup and ImageContent block."""
    result_blocks = auto_design(
        template_id="rc_lowpass_1st",
        specs={"f_3dB_hz": {"target": 1000, "tolerance_pct": 10}},
        sim_type="ac",
    )
    result = _extract_metadata(result_blocks)
    assert result["status"] == "ok"
    assert "svg_content" in result
    assert result["svg_content"].lstrip().startswith(("<?xml", "<svg"))
    # Verify ImageContent block is present
    assert len(result_blocks) == 2
    assert isinstance(result_blocks[1], ImageContent)
    assert result_blocks[1].mimeType == "image/svg+xml"
    decoded = base64.b64decode(result_blocks[1].data).decode("utf-8")
    assert decoded.lstrip().startswith(("<?xml", "<svg"))
