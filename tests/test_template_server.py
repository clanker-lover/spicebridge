"""Integration tests for template-related MCP tools in spicebridge.server."""

from spicebridge.server import (
    create_circuit,
    list_templates,
    load_template,
    modify_component,
    run_ac_analysis,
    run_dc_op,
    validate_netlist,
)
from spicebridge.standard_values import snap_to_standard

# --- list_templates ---


def test_list_templates_all():
    result = list_templates()
    assert result["status"] == "ok"
    assert result["count"] >= 5
    ids = [t["id"] for t in result["templates"]]
    assert "rc_lowpass_1st" in ids
    assert "voltage_divider" in ids


def test_list_templates_filter_category():
    result = list_templates(category="filters")
    assert result["status"] == "ok"
    assert result["count"] == 6
    assert all(t["category"] == "filters" for t in result["templates"])


# --- load_template ---


def test_load_template_default_params():
    result = load_template("rc_lowpass_1st")
    assert result["status"] == "ok"
    assert len(result["circuit_id"]) == 8
    assert "R1" in result["components"]
    assert len(result["design_equations"]) > 0
    # Default .param R1=10k should be in the preview
    assert any("R1=10k" in line for line in result["preview"])


def test_load_template_custom_params():
    result = load_template("rc_lowpass_1st", params={"R1": "22k"})
    assert result["status"] == "ok"
    assert any("R1=22k" in line for line in result["preview"])


def test_load_template_invalid_id():
    result = load_template("nonexistent")
    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_load_template_then_simulate_ac():
    """Load rc_lowpass_1st and run AC analysis end-to-end."""
    loaded = load_template("rc_lowpass_1st")
    assert loaded["status"] == "ok"
    cid = loaded["circuit_id"]

    ac = run_ac_analysis(cid, start_freq=1.0, stop_freq=1e6, points_per_decade=10)
    assert ac["status"] == "ok"
    assert "f_3dB_hz" in ac["results"]
    # Default R1=10k, C1=10n -> f_c ~ 1592 Hz
    assert abs(ac["results"]["f_3dB_hz"] - 1592) < 200


# --- modify_component ---


def test_modify_component_updates_netlist():
    loaded = load_template("voltage_divider")
    cid = loaded["circuit_id"]

    result = modify_component(cid, "R2", "20k")
    assert result["status"] == "ok"
    assert result["circuit_id"] == cid
    # The preview should show the updated param
    assert any("R2=20k" in line for line in result["preview"])


def test_modify_component_invalid_circuit():
    result = modify_component("deadbeef", "R1", "1k")
    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_modify_component_not_found():
    loaded = load_template("voltage_divider")
    cid = loaded["circuit_id"]

    result = modify_component(cid, "C99", "100n")
    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_modify_component_then_simulate_dc():
    """Load voltage_divider, change R2 to 30k, and run DC OP."""
    loaded = load_template("voltage_divider")
    cid = loaded["circuit_id"]

    modify_component(cid, "R2", "30k")

    dc = run_dc_op(cid)
    assert dc["status"] == "ok"
    # V_out = 10 * 30k / (10k + 30k) = 7.5 V
    assert abs(dc["results"]["nodes"]["v(out)"] - 7.5) < 0.1


# --- validate_netlist ---


def test_validate_valid_circuit():
    loaded = load_template("voltage_divider")
    cid = loaded["circuit_id"]

    result = validate_netlist(cid)
    assert result["status"] == "ok"
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_broken_netlist():
    """A netlist with garbage should fail validation."""
    created = create_circuit("THIS IS NOT A VALID NETLIST\nXYZ123 GARBAGE")
    cid = created["circuit_id"]

    result = validate_netlist(cid)
    assert result["status"] == "ok"
    assert result["valid"] is False
    assert len(result["errors"]) > 0


def test_validate_invalid_circuit_id():
    result = validate_netlist("deadbeef")
    assert result["status"] == "error"
    assert "not found" in result["error"]


# --- load_template with specs ---


def test_load_template_with_specs():
    """specs triggers solver, returns calculated_values, preview shows new values."""
    result = load_template("rc_lowpass_1st", specs={"f_cutoff_hz": 1000})
    assert result["status"] == "ok"
    assert "calculated_values" in result
    assert "R1" in result["calculated_values"]
    assert "C1" in result["calculated_values"]
    assert "solver_notes" in result


def test_load_template_no_specs_unchanged():
    """Without specs, response has no calculated_values key."""
    result = load_template("rc_lowpass_1st")
    assert result["status"] == "ok"
    assert "calculated_values" not in result


def test_load_template_specs_with_params_override():
    """Explicit params override solver-calculated values."""
    result = load_template(
        "rc_lowpass_1st",
        specs={"f_cutoff_hz": 1000},
        params={"R1": "47k"},
    )
    assert result["status"] == "ok"
    # R1 should show the explicit override in the preview
    full = "\n".join(result["preview"])
    assert "R1=47k" in full
    # But calculated_values reflects solver output (pre-override)
    assert result["calculated_values"]["R1"] != "47k"


def test_load_template_specs_invalid():
    """Bad specs return an error."""
    result = load_template("rc_lowpass_1st", specs={})
    assert result["status"] == "error"
    assert "requires" in result["error"]


def test_load_template_specs_values_are_e24():
    """Calculated values should be snapped to E24 standard values."""
    result = load_template("rc_lowpass_1st", specs={"f_cutoff_hz": 1000})
    assert result["status"] == "ok"
    from spicebridge.standard_values import parse_spice_value as _parse_spice_value

    for name, val_str in result["calculated_values"].items():
        numeric = _parse_spice_value(val_str)
        snapped = snap_to_standard(numeric, "E24")
        assert abs(numeric - snapped) / snapped < 0.001, (
            f"{name}={val_str} ({numeric}) not E24 (nearest {snapped})"
        )


def test_load_template_specs_end_to_end():
    """Load with specs, simulate, verify cutoff within 10% of target."""
    target_fc = 5000
    loaded = load_template("rc_lowpass_1st", specs={"f_cutoff_hz": target_fc})
    assert loaded["status"] == "ok"
    cid = loaded["circuit_id"]

    ac = run_ac_analysis(cid, start_freq=1.0, stop_freq=1e6, points_per_decade=20)
    assert ac["status"] == "ok"
    measured_fc = ac["results"]["f_3dB_hz"]
    assert abs(measured_fc - target_fc) / target_fc < 0.10, (
        f"Cutoff {measured_fc} Hz not within 10% of target {target_fc} Hz"
    )
