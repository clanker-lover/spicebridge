"""Unit and integration tests for Monte Carlo and worst-case analysis."""

from __future__ import annotations

import random

import pytest

from spicebridge.monte_carlo import (
    apply_corner,
    build_analysis_cmd,
    compute_statistics,
    generate_corners,
    parse_component_values,
    randomize_values,
    substitute_values,
)
from spicebridge.standard_values import format_engineering, parse_spice_value

# ---------------------------------------------------------------------------
# Test netlists
# ---------------------------------------------------------------------------

RC_LOWPASS = """\
* RC Low-Pass Filter
V1 in 0 dc 0 ac 1
R1 in out 1k
C1 out 0 100n
"""

RC_PARAM = """\
* RC with .param
.param R1 = 1k
.param C1 = 100n
V1 in 0 dc 0 ac 1
R1 in out {R1}
C1 out 0 {C1}
"""

RC_INSTANCE_ONLY = """\
* RC Instance Only
V1 in 0 dc 0 ac 1
R1 in out 10k
C1 out 0 15.9n
L1 a b 4.7u
"""

PARAM_WINS = """\
* Param wins over instance
.param R1 = 2.2k
V1 in 0 dc 0 ac 1
R1 in out 1k
C1 out 0 100n
"""

PARAMETERIZED = """\
* Parameterized values should be skipped
V1 in 0 dc 0 ac 1
R1 in out {R_val}
C1 out 0 100n
"""

VOLTAGE_ONLY = """\
* Only voltage source, no R/C/L
V1 in 0 dc 5
"""

WITH_IC = """\
* Instance line with IC=0
V1 in 0 dc 0 ac 1
R1 in out 1k
C1 out 0 100n IC=0
"""


# ---------------------------------------------------------------------------
# Unit tests: parse_spice_value
# ---------------------------------------------------------------------------


class TestParseSpiceValue:
    def test_suffixes(self):
        assert parse_spice_value("10k") == pytest.approx(10000.0)
        assert parse_spice_value("15.9n") == pytest.approx(1.59e-8)
        assert parse_spice_value("4.7meg") == pytest.approx(4.7e6)
        assert parse_spice_value("100u") == pytest.approx(100e-6)
        assert parse_spice_value("1p") == pytest.approx(1e-12)
        assert parse_spice_value("2.2f") == pytest.approx(2.2e-15)
        assert parse_spice_value("3.3") == pytest.approx(3.3)
        assert parse_spice_value("47") == pytest.approx(47.0)

    def test_case_insensitive(self):
        assert parse_spice_value("10K") == 10000.0
        assert parse_spice_value("4.7MEG") == 4.7e6

    def test_whitespace(self):
        assert parse_spice_value("  1k  ") == 1000.0


# ---------------------------------------------------------------------------
# Unit tests: parse_component_values
# ---------------------------------------------------------------------------


class TestParseComponentValues:
    def test_instance_lines(self):
        comps = parse_component_values(RC_INSTANCE_ONLY)
        refs = {c.ref for c in comps}
        assert refs == {"R1", "C1", "L1"}
        r1 = next(c for c in comps if c.ref == "R1")
        assert r1.value == 10000.0
        assert r1.source == "instance"

    def test_param_lines(self):
        comps = parse_component_values(RC_PARAM)
        param_comps = [c for c in comps if c.source == "param"]
        refs = {c.ref for c in param_comps}
        assert "R1" in refs
        assert "C1" in refs

    def test_param_wins_over_instance(self):
        comps = parse_component_values(PARAM_WINS)
        r1 = next(c for c in comps if c.ref == "R1")
        assert r1.value == 2200.0
        assert r1.source == "param"

    def test_skips_parameterized(self):
        comps = parse_component_values(PARAMETERIZED)
        refs = {c.ref for c in comps}
        assert "R1" not in refs
        assert "C1" in refs

    def test_skips_voltage_sources(self):
        comps = parse_component_values(VOLTAGE_ONLY)
        assert len(comps) == 0

    def test_instance_with_ic(self):
        comps = parse_component_values(WITH_IC)
        c1 = next(c for c in comps if c.ref == "C1")
        assert abs(c1.value - 100e-9) < 1e-15

    def test_rc_lowpass(self):
        comps = parse_component_values(RC_LOWPASS)
        refs = {c.ref for c in comps}
        assert refs == {"R1", "C1"}


# ---------------------------------------------------------------------------
# Unit tests: randomize_values
# ---------------------------------------------------------------------------


class TestRandomizeValues:
    def test_seed_reproducibility(self):
        comps = parse_component_values(RC_LOWPASS)
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        v1 = randomize_values(comps, None, 5.0, rng1)
        v2 = randomize_values(comps, None, 5.0, rng2)
        assert v1 == v2

    def test_distribution(self):
        comps = parse_component_values(RC_LOWPASS)
        r1 = next(c for c in comps if c.ref == "R1")
        tol_pct = 10.0
        samples = []
        rng = random.Random(123)
        for _ in range(10000):
            vals = randomize_values(comps, None, tol_pct, rng)
            samples.append(vals["R1"])

        mean = sum(samples) / len(samples)
        std = (sum((s - mean) ** 2 for s in samples) / len(samples)) ** 0.5

        # Mean should be approximately nominal
        assert abs(mean - r1.value) / r1.value < 0.02
        # Std should be approximately tol/3 * nominal
        expected_std = r1.value * tol_pct / 100.0 / 3.0
        assert abs(std - expected_std) / expected_std < 0.1


# ---------------------------------------------------------------------------
# Unit tests: apply_corner
# ---------------------------------------------------------------------------


class TestApplyCorner:
    def test_plus_minus(self):
        comps = parse_component_values(RC_LOWPASS)
        tol = 5.0

        # All +1: each component at +tol%
        corner_plus = tuple(1 for _ in comps)
        vals_plus = apply_corner(comps, None, tol, corner_plus)
        for comp in comps:
            assert vals_plus[comp.ref] == pytest.approx(comp.value * 1.05)

        # All -1: each component at -tol%
        corner_minus = tuple(-1 for _ in comps)
        vals_minus = apply_corner(comps, None, tol, corner_minus)
        for comp in comps:
            assert vals_minus[comp.ref] == pytest.approx(comp.value * 0.95)

        # Zero direction: nominal
        corner_zero = tuple(0 for _ in comps)
        vals_zero = apply_corner(comps, None, tol, corner_zero)
        for comp in comps:
            assert vals_zero[comp.ref] == pytest.approx(comp.value)


# ---------------------------------------------------------------------------
# Unit tests: generate_corners
# ---------------------------------------------------------------------------


class TestGenerateCorners:
    def test_two_components(self):
        corners = generate_corners(2)
        assert len(corners) == 4
        assert (-1, -1) in corners
        assert (-1, 1) in corners
        assert (1, -1) in corners
        assert (1, 1) in corners

    def test_three_components(self):
        corners = generate_corners(3)
        assert len(corners) == 8


# ---------------------------------------------------------------------------
# Unit tests: substitute_values
# ---------------------------------------------------------------------------


class TestSubstituteValues:
    def test_instance_substitution(self):
        comps = parse_component_values(RC_LOWPASS)
        values = {c.ref: c.value * 1.1 for c in comps}
        result = substitute_values(RC_LOWPASS, comps, values)
        # R1 should now be 1.1k
        assert "1.1k" in result
        # Title and V1 lines should be unchanged
        assert "* RC Low-Pass Filter" in result
        assert "V1 in 0 dc 0 ac 1" in result

    def test_param_substitution(self):
        netlist = """\
* Test
.param R1 = 1k
V1 in 0 dc 0 ac 1
R1 in out {R1}
C1 out 0 100n"""
        comps = parse_component_values(netlist)
        # Only R1 should be found (from .param), C1 from instance
        r1 = next(c for c in comps if c.ref == "R1")
        assert r1.source == "param"
        values = {"R1": 2200.0, "C1": 100e-9}
        result = substitute_values(netlist, comps, values)
        assert "2.2k" in result


# ---------------------------------------------------------------------------
# Unit tests: compute_statistics
# ---------------------------------------------------------------------------


class TestComputeStatistics:
    def test_known_data(self):
        results = [
            {"f_3dB_hz": 1000.0, "gain_dB": -3.0},
            {"f_3dB_hz": 1100.0, "gain_dB": -2.8},
            {"f_3dB_hz": 900.0, "gain_dB": -3.2},
            {"f_3dB_hz": 1050.0, "gain_dB": -2.9},
        ]
        stats = compute_statistics(results)
        assert "f_3dB_hz" in stats
        s = stats["f_3dB_hz"]
        assert s["mean"] == pytest.approx(1012.5)
        assert s["min"] == pytest.approx(900.0)
        assert s["max"] == pytest.approx(1100.0)
        assert s["std"] > 0
        assert "median" in s
        assert "pct_5" in s
        assert "pct_95" in s

    def test_empty(self):
        stats = compute_statistics([])
        assert stats == {}

    def test_dc_op_nodes_flattened(self):
        results = [
            {"nodes": {"v(out)": 4.9}, "analysis_type": "Operating Point"},
            {"nodes": {"v(out)": 5.1}, "analysis_type": "Operating Point"},
        ]
        stats = compute_statistics(results)
        assert "nodes.v(out)" in stats
        s = stats["nodes.v(out)"]
        assert s["mean"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Unit tests: build_analysis_cmd
# ---------------------------------------------------------------------------


class TestBuildAnalysisCmd:
    def test_ac(self):
        cmd = build_analysis_cmd("ac")
        assert cmd == ".ac dec 10 1 1000000.0"

    def test_ac_custom(self):
        cmd = build_analysis_cmd(
            "ac", points_per_decade=20, start_freq=100, stop_freq=1e9
        )
        assert "20" in cmd
        assert "100" in cmd

    def test_transient(self):
        cmd = build_analysis_cmd("transient", step_time=1e-6, stop_time=1e-3)
        assert ".tran" in cmd
        assert "1e-06" in cmd or "1e-6" in cmd

    def test_dc_op(self):
        cmd = build_analysis_cmd("dc_op")
        assert cmd == ".op"

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            build_analysis_cmd("invalid")


# ---------------------------------------------------------------------------
# Unit tests: format_engineering roundtrip
# ---------------------------------------------------------------------------


class TestFormatEngineeringRoundtrip:
    @pytest.mark.parametrize(
        "original",
        ["10k", "1k", "100n", "4.7u", "15.9n", "47"],
    )
    def test_roundtrip(self, original):
        value = parse_spice_value(original)
        formatted = format_engineering(value)
        reparsed = parse_spice_value(formatted)
        assert reparsed == pytest.approx(value, rel=1e-3)


# ---------------------------------------------------------------------------
# Integration tests (require ngspice)
# ---------------------------------------------------------------------------


class TestMonteCarloIntegration:
    def test_rc_lowpass(self):
        from spicebridge.server import create_circuit, run_monte_carlo

        result = create_circuit(RC_LOWPASS)
        assert result["status"] == "ok"
        cid = result["circuit_id"]

        mc = run_monte_carlo(
            cid,
            analysis_type="ac",
            num_runs=20,
            default_tolerance_pct=10.0,
            seed=42,
            start_freq=1.0,
            stop_freq=1e6,
            points_per_decade=10,
        )
        assert mc["status"] == "ok"
        assert mc["num_successful"] == 20
        assert mc["num_failed"] == 0
        stats = mc["statistics"]
        assert "f_3dB_hz" in stats
        # Nominal f_3dB ≈ 1/(2*pi*1k*100n) ≈ 1592 Hz
        assert abs(stats["f_3dB_hz"]["mean"] - 1592) < 300
        assert stats["f_3dB_hz"]["std"] > 0

    def test_seed_reproducibility_e2e(self):
        from spicebridge.server import create_circuit, run_monte_carlo

        result = create_circuit(RC_LOWPASS)
        cid = result["circuit_id"]

        mc1 = run_monte_carlo(
            cid, analysis_type="ac", num_runs=5, seed=99, default_tolerance_pct=5.0
        )
        mc2 = run_monte_carlo(
            cid, analysis_type="ac", num_runs=5, seed=99, default_tolerance_pct=5.0
        )
        assert mc1["statistics"] == mc2["statistics"]

    def test_no_components_error(self):
        from spicebridge.server import create_circuit, run_monte_carlo

        result = create_circuit(VOLTAGE_ONLY)
        cid = result["circuit_id"]

        mc = run_monte_carlo(cid, analysis_type="dc_op", num_runs=5)
        assert mc["status"] == "error"
        assert "No R/C/L" in mc["error"]


class TestWorstCaseIntegration:
    def test_rc_lowpass(self):
        from spicebridge.server import create_circuit, run_worst_case

        result = create_circuit(RC_LOWPASS)
        assert result["status"] == "ok"
        cid = result["circuit_id"]

        wc = run_worst_case(
            cid,
            analysis_type="ac",
            default_tolerance_pct=5.0,
            start_freq=1.0,
            stop_freq=1e6,
            points_per_decade=10,
        )
        assert wc["status"] == "ok"
        assert wc["strategy"] == "exhaustive"
        assert "nominal" in wc
        assert "worst_case" in wc
        assert "sensitivity" in wc

        # f_3dB worst-case min should be less than nominal, max greater
        if "f_3dB_hz" in wc["worst_case"]:
            wc_f3db = wc["worst_case"]["f_3dB_hz"]
            nom_f3db = wc["nominal"]["f_3dB_hz"]
            assert wc_f3db["min"] < nom_f3db
            assert wc_f3db["max"] > nom_f3db

    def test_sensitivity_values(self):
        from spicebridge.server import create_circuit, run_worst_case

        result = create_circuit(RC_LOWPASS)
        cid = result["circuit_id"]

        wc = run_worst_case(
            cid,
            analysis_type="ac",
            default_tolerance_pct=5.0,
            start_freq=1.0,
            stop_freq=1e6,
            points_per_decade=10,
        )
        assert wc["status"] == "ok"
        sens = wc["sensitivity"]
        # f_3dB should have sensitivity entries
        assert "f_3dB_hz" in sens
        entries = sens["f_3dB_hz"]
        assert len(entries) > 0
        # Both R1 and C1 should have nonzero sensitivity to f_3dB
        comp_refs = {e["component"] for e in entries}
        assert "R1" in comp_refs
        assert "C1" in comp_refs
        for e in entries:
            if e["component"] in ("R1", "C1"):
                assert e["pct_per_pct"] != 0
                # Magnitude ≈ 1.0 (inverse relationship)
                assert abs(e["pct_per_pct"]) == pytest.approx(1.0, abs=0.2)

    def test_nominal_failure(self):
        from spicebridge.server import create_circuit, run_worst_case

        # Create a circuit with bad netlist
        bad_netlist = """\
* Bad circuit
R1 in out 1k
"""
        result = create_circuit(bad_netlist)
        cid = result["circuit_id"]

        wc = run_worst_case(cid, analysis_type="ac")
        assert wc["status"] == "error"
        assert "Nominal simulation failed" in wc["error"]
