"""Tests for standard_values, solver, and calculate_components MCP tool."""

from __future__ import annotations

import math

import pytest

from spicebridge.server import calculate_components
from spicebridge.solver import solve
from spicebridge.standard_values import (
    format_engineering,
    snap_to_standard,
)

# ===========================================================================
# standard_values: snap_to_standard
# ===========================================================================


class TestSnapToStandard:
    def test_exact_e12_value(self):
        assert snap_to_standard(4.7e3, "E12") == 4.7e3

    def test_midpoint_rounds_to_nearest(self):
        # Between 4.7k and 5.6k (E12) — 5.1k is closer to 4.7k in log space
        result = snap_to_standard(5.1e3, "E12")
        assert result in (4.7e3, 5.6e3)

    def test_e96_precision(self):
        # 4.99k is an E96 value
        assert snap_to_standard(4.99e3, "E96") == pytest.approx(4.99e3)

    def test_sub_ohm(self):
        result = snap_to_standard(0.47, "E12")
        assert result == pytest.approx(0.47)

    def test_megohm(self):
        result = snap_to_standard(1e6, "E12")
        assert result == pytest.approx(1e6)

    def test_picofarad_scale(self):
        result = snap_to_standard(100e-12, "E12")
        assert result == pytest.approx(100e-12)

    def test_decade_boundary(self):
        # Value near 9.76 in E96 — should not jump a decade erroneously
        result = snap_to_standard(9.76e3, "E96")
        assert result == pytest.approx(9.76e3)

    def test_value_near_10(self):
        # 9.9k is closer to 10k (E12 first value of next decade) than 8.2k
        result = snap_to_standard(9.9e3, "E12")
        assert result == pytest.approx(10e3)

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="positive"):
            snap_to_standard(-1.0)

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="positive"):
            snap_to_standard(0.0)

    def test_unknown_series_raises(self):
        with pytest.raises(ValueError, match="Unknown series"):
            snap_to_standard(1.0, "E48")


# ===========================================================================
# standard_values: format_engineering
# ===========================================================================


class TestFormatEngineering:
    def test_kilohm(self):
        assert format_engineering(10e3) == "10k"

    def test_megohm(self):
        assert format_engineering(1e6) == "1M"

    def test_nanofarad(self):
        assert format_engineering(15.9e-9) == "15.9n"

    def test_picofarad(self):
        assert format_engineering(100e-12) == "100p"

    def test_microfarad(self):
        assert format_engineering(4.7e-6) == "4.7u"

    def test_plain_value(self):
        assert format_engineering(100.0) == "100"

    def test_fractional_k(self):
        assert format_engineering(1.5e3) == "1.5k"

    def test_zero(self):
        assert format_engineering(0) == "0"


# ===========================================================================
# solver: RC lowpass 1st order
# ===========================================================================


class TestRCLowpass1st:
    def test_1khz(self):
        result = solve("rc_lowpass_1st", {"f_cutoff_hz": 1000})
        assert "R1" in result["components"]
        assert "C1" in result["components"]
        assert result["nearest_standard"]["series"] == "E96"

    def test_roundtrip_1khz(self):
        """Verify calculated values satisfy the design equation."""
        result = solve("rc_lowpass_1st", {"f_cutoff_hz": 1000})
        # Parse raw values from formatted strings — use internal calculation
        r1, c1 = _parse_rc(result)
        f_calc = 1.0 / (2 * math.pi * r1 * c1)
        assert f_calc == pytest.approx(1000, rel=0.01)

    def test_classic_1592hz(self):
        """10k + 10nF gives 1/(2*pi*10k*10n) ≈ 1592 Hz."""
        result = solve("rc_lowpass_1st", {"f_cutoff_hz": 1592})
        r1, c1 = _parse_rc(result)
        f_calc = 1.0 / (2 * math.pi * r1 * c1)
        assert f_calc == pytest.approx(1592, rel=0.01)

    def test_very_high_freq(self):
        result = solve("rc_lowpass_1st", {"f_cutoff_hz": 10e6})
        assert "R1" in result["components"]

    def test_missing_spec(self):
        with pytest.raises(ValueError, match="requires"):
            solve("rc_lowpass_1st", {})

    def test_negative_freq(self):
        with pytest.raises(ValueError, match="positive"):
            solve("rc_lowpass_1st", {"f_cutoff_hz": -100})


# ===========================================================================
# solver: RC highpass 1st order
# ===========================================================================


class TestRCHighpass1st:
    def test_1khz(self):
        result = solve("rc_highpass_1st", {"f_cutoff_hz": 1000})
        r1, c1 = _parse_rc(result)
        f_calc = 1.0 / (2 * math.pi * r1 * c1)
        assert f_calc == pytest.approx(1000, rel=0.01)

    def test_missing_spec(self):
        with pytest.raises(ValueError, match="requires"):
            solve("rc_highpass_1st", {})


# ===========================================================================
# solver: Sallen-Key lowpass 2nd order
# ===========================================================================


class TestSallenKeyLowpass2nd:
    def test_butterworth_1khz(self):
        result = solve("sallen_key_lowpass_2nd", {"f_cutoff_hz": 1000})
        c = result["components"]
        # Equal-R design: R1 == R2
        assert c["R1"] == c["R2"]

    def test_butterworth_c_ratio(self):
        """For Butterworth Q=0.707: C1 ≈ 2*C2."""
        result = solve("sallen_key_lowpass_2nd", {"f_cutoff_hz": 1000})
        c1, c2 = _parse_sallen_caps(result)
        assert c1 / c2 == pytest.approx(2.0, rel=0.01)

    def test_cutoff_equation(self):
        """Verify f_c = 1/(2*pi*sqrt(R1*R2*C1*C2))."""
        result = solve("sallen_key_lowpass_2nd", {"f_cutoff_hz": 1000})
        r1, r2, c1, c2 = _parse_sallen_all(result)
        f_calc = 1.0 / (2 * math.pi * math.sqrt(r1 * r2 * c1 * c2))
        assert f_calc == pytest.approx(1000, rel=0.01)

    def test_custom_q(self):
        result = solve("sallen_key_lowpass_2nd", {"f_cutoff_hz": 1000, "Q": 1.0})
        c1, c2 = _parse_sallen_caps(result)
        # C1 = 4*Q^2*C2 = 4*C2 when Q=1
        assert c1 / c2 == pytest.approx(4.0, rel=0.01)

    def test_missing_spec(self):
        with pytest.raises(ValueError, match="requires"):
            solve("sallen_key_lowpass_2nd", {})


# ===========================================================================
# solver: Inverting opamp
# ===========================================================================


class TestInvertingOpamp:
    def test_20db(self):
        result = solve("inverting_opamp", {"gain_dB": 20})
        # 20 dB = gain of 10, Rin=10k default -> Rf=100k
        assert result["components"]["Rf"] == "100k"
        assert result["components"]["Rin"] == "10k"

    def test_gain_linear(self):
        result = solve("inverting_opamp", {"gain_linear": -5})
        # |gain| = 5, Rin=10k -> Rf=50k
        assert result["components"]["Rf"] == "50k"

    def test_custom_impedance(self):
        result = solve("inverting_opamp", {"gain_dB": 20, "input_impedance_ohms": 1e3})
        assert result["components"]["Rin"] == "1k"
        assert result["components"]["Rf"] == "10k"

    def test_both_specs_error(self):
        with pytest.raises(ValueError, match="exactly one"):
            solve("inverting_opamp", {"gain_dB": 20, "gain_linear": 10})

    def test_no_specs_error(self):
        with pytest.raises(ValueError, match="requires"):
            solve("inverting_opamp", {})


# ===========================================================================
# solver: Non-inverting opamp
# ===========================================================================


class TestNoninvertingOpamp:
    def test_20db(self):
        result = solve("noninverting_opamp", {"gain_dB": 20})
        # 20 dB = 10x, R1=10k -> R2 = 9*10k = 90k
        assert result["components"]["R1"] == "10k"
        assert result["components"]["R2"] == "90k"

    def test_unity_gain(self):
        result = solve("noninverting_opamp", {"gain_linear": 1})
        assert result["components"]["R1"] == "open"
        assert result["components"]["R2"] == "0"
        assert "buffer" in result["notes"][0].lower()

    def test_gain_less_than_1_error(self):
        with pytest.raises(ValueError, match=">= 1"):
            solve("noninverting_opamp", {"gain_linear": 0.5})


# ===========================================================================
# solver: Voltage divider
# ===========================================================================


class TestVoltageDivider:
    def test_ratio_half(self):
        result = solve("voltage_divider", {"ratio": 0.5})
        assert result["components"]["R1"] == "10k"
        assert result["components"]["R2"] == "10k"

    def test_voltage_specs(self):
        """3.3V from 5V -> ratio = 0.66."""
        result = solve("voltage_divider", {"output_voltage": 3.3, "input_voltage": 5.0})
        r1, r2 = _parse_divider(result)
        actual_ratio = r2 / (r1 + r2)
        assert actual_ratio == pytest.approx(3.3 / 5.0, rel=0.01)

    def test_out_of_range_ratio_high(self):
        with pytest.raises(ValueError, match="between 0 and 1"):
            solve("voltage_divider", {"ratio": 1.0})

    def test_out_of_range_ratio_low(self):
        with pytest.raises(ValueError, match="between 0 and 1"):
            solve("voltage_divider", {"ratio": 0.0})

    def test_missing_specs(self):
        with pytest.raises(ValueError, match="requires"):
            solve("voltage_divider", {})


# ===========================================================================
# solver: MFB Bandpass
# ===========================================================================


class TestMFBBandpass:
    def test_1khz_default(self):
        result = solve("mfb_bandpass", {"f_center_hz": 1000})
        c = result["components"]
        assert "R1" in c
        assert "R2" in c
        assert "R3" in c
        assert "C1" in c
        assert "C2" in c

    def test_roundtrip_f_center(self):
        """Verify f0 = Q / (pi * C * R3) from R3 = 2*Q/(2*pi*f0*C)."""
        result = solve("mfb_bandpass", {"f_center_hz": 1000})
        r1, r2, r3, c1, c2 = _parse_mfb(result)
        c = c1  # equal-C
        q = 1.0  # default Q
        f_calc = q / (math.pi * c * r3)
        assert f_calc == pytest.approx(1000, rel=0.01)

    def test_custom_q(self):
        result = solve("mfb_bandpass", {"f_center_hz": 1000, "Q": 5.0})
        r1, r2, r3, c1, c2 = _parse_mfb(result)
        q_calc = math.pi * 1000 * c1 * r3
        assert q_calc == pytest.approx(5.0, rel=0.01)

    def test_custom_gain(self):
        result = solve("mfb_bandpass", {"f_center_hz": 1000, "gain_linear": 2.0})
        r1, r2, r3, c1, c2 = _parse_mfb(result)
        gain_calc = r3 / (2.0 * r1)
        assert gain_calc == pytest.approx(2.0, rel=0.01)

    def test_equal_c(self):
        result = solve("mfb_bandpass", {"f_center_hz": 1000})
        c = result["components"]
        assert c["C1"] == c["C2"]

    def test_missing_spec(self):
        with pytest.raises(ValueError, match="requires"):
            solve("mfb_bandpass", {})

    def test_negative_freq(self):
        with pytest.raises(ValueError, match="positive"):
            solve("mfb_bandpass", {"f_center_hz": -100})


# ===========================================================================
# solver: Sallen-Key HPF 2nd order
# ===========================================================================


class TestSallenKeyHPF2nd:
    def test_butterworth_1khz(self):
        result = solve("sallen_key_hpf_2nd", {"f_cutoff_hz": 1000})
        c = result["components"]
        assert c["R1"] == c["R2"]

    def test_equal_r(self):
        result = solve("sallen_key_hpf_2nd", {"f_cutoff_hz": 1000})
        c = result["components"]
        assert c["R1"] == c["R2"]

    def test_c_ratio(self):
        """For Butterworth Q=0.707: C1 ~ 2*C2."""
        result = solve("sallen_key_hpf_2nd", {"f_cutoff_hz": 1000})
        c1, c2 = _parse_hpf_caps(result)
        assert c1 / c2 == pytest.approx(2.0, rel=0.01)

    def test_cutoff_equation(self):
        """Verify f_c = 1/(2*pi*sqrt(R1*R2*C1*C2))."""
        result = solve("sallen_key_hpf_2nd", {"f_cutoff_hz": 1000})
        r1, r2, c1, c2 = _parse_sallen_all(result)
        f_calc = 1.0 / (2 * math.pi * math.sqrt(r1 * r2 * c1 * c2))
        assert f_calc == pytest.approx(1000, rel=0.01)

    def test_custom_q(self):
        result = solve("sallen_key_hpf_2nd", {"f_cutoff_hz": 1000, "Q": 1.0})
        c1, c2 = _parse_hpf_caps(result)
        assert c1 / c2 == pytest.approx(4.0, rel=0.01)

    def test_missing_spec(self):
        with pytest.raises(ValueError, match="requires"):
            solve("sallen_key_hpf_2nd", {})


# ===========================================================================
# solver: Summing Amplifier
# ===========================================================================


class TestSummingAmplifier:
    def test_default_3_input(self):
        result = solve("summing_amplifier", {})
        c = result["components"]
        assert "R1" in c
        assert "R2" in c
        assert "R3" in c
        assert "Rf" in c
        assert "R4" not in c

    def test_2_inputs(self):
        result = solve("summing_amplifier", {"num_inputs": 2})
        c = result["components"]
        assert "R1" in c
        assert "R2" in c
        assert "R3" not in c

    def test_6_inputs(self):
        result = solve("summing_amplifier", {"num_inputs": 6})
        c = result["components"]
        for i in range(1, 7):
            assert f"R{i}" in c
        assert "R7" not in c

    def test_custom_gain(self):
        result = solve("summing_amplifier", {"gain_per_input": 5.0})
        c = result["components"]
        rf = _parse_eng(c["Rf"])
        r1 = _parse_eng(c["R1"])
        assert rf / r1 == pytest.approx(5.0, rel=0.01)

    def test_custom_impedance(self):
        result = solve(
            "summing_amplifier", {"input_impedance_ohms": 20e3, "gain_per_input": 2.0}
        )
        c = result["components"]
        assert c["R1"] == "20k"
        assert c["Rf"] == "40k"

    def test_inputs_out_of_range(self):
        with pytest.raises(ValueError, match="between 2 and 6"):
            solve("summing_amplifier", {"num_inputs": 7})
        with pytest.raises(ValueError, match="between 2 and 6"):
            solve("summing_amplifier", {"num_inputs": 1})


# ===========================================================================
# solver: Differential Amplifier
# ===========================================================================


class TestDifferentialAmp:
    def test_default_unity(self):
        result = solve("differential_amp", {})
        c = result["components"]
        assert c["R1"] == c["R2"]
        assert c["R3"] == c["R4"]
        assert c["R1"] == c["R3"]

    def test_gain_10(self):
        result = solve("differential_amp", {"gain_linear": 10.0})
        c = result["components"]
        r1 = _parse_eng(c["R1"])
        r2 = _parse_eng(c["R2"])
        assert r2 / r1 == pytest.approx(10.0, rel=0.01)

    def test_custom_impedance(self):
        result = solve(
            "differential_amp", {"input_impedance_ohms": 5e3, "gain_linear": 2.0}
        )
        c = result["components"]
        assert c["R1"] == "5k"
        assert c["R2"] == "10k"

    def test_matched_ratios(self):
        result = solve("differential_amp", {"gain_linear": 3.0})
        c = result["components"]
        r1 = _parse_eng(c["R1"])
        r2 = _parse_eng(c["R2"])
        r3 = _parse_eng(c["R3"])
        r4 = _parse_eng(c["R4"])
        assert r2 / r1 == pytest.approx(r4 / r3, rel=0.01)


# ===========================================================================
# solver: Instrumentation Amplifier
# ===========================================================================


class TestInstrumentationAmp:
    def test_gain_10(self):
        result = solve("instrumentation_amp", {"gain_linear": 10.0})
        c = result["components"]
        assert "Rg" in c
        assert c["Rg"] != "open"

    def test_unity_gain(self):
        result = solve("instrumentation_amp", {"gain_linear": 1.0})
        c = result["components"]
        assert c["Rg"] == "open"

    def test_rg_calculation(self):
        """Rg = 2*R1 / (gain - 1)."""
        result = solve("instrumentation_amp", {"gain_linear": 10.0})
        c = result["components"]
        r1 = _parse_eng(c["R1"])
        rg = _parse_eng(c["Rg"])
        expected_rg = 2.0 * r1 / (10.0 - 1)
        assert rg == pytest.approx(expected_rg, rel=0.01)

    def test_missing_spec(self):
        with pytest.raises(ValueError, match="requires"):
            solve("instrumentation_amp", {})

    def test_gain_below_1(self):
        with pytest.raises(ValueError, match=">= 1"):
            solve("instrumentation_amp", {"gain_linear": 0.5})


# ===========================================================================
# solver: Twin-T Notch
# ===========================================================================


class TestTwinTNotch:
    def test_1khz(self):
        result = solve("twin_t_notch", {"f_notch_hz": 1000})
        c = result["components"]
        assert "R" in c
        assert "R_half" in c
        assert "C" in c
        assert "C_dbl" in c

    def test_roundtrip_f_notch(self):
        """Verify f = 1/(2*pi*R*C)."""
        result = solve("twin_t_notch", {"f_notch_hz": 1000})
        r, r_half, c, c_dbl = _parse_twin_t(result)
        f_calc = 1.0 / (2.0 * math.pi * r * c)
        assert f_calc == pytest.approx(1000, rel=0.01)

    def test_r_half_and_c_dbl(self):
        result = solve("twin_t_notch", {"f_notch_hz": 1000})
        r, r_half, c, c_dbl = _parse_twin_t(result)
        assert r_half == pytest.approx(r / 2.0, rel=0.01)
        assert c_dbl == pytest.approx(2.0 * c, rel=0.01)

    def test_missing_spec(self):
        with pytest.raises(ValueError, match="requires"):
            solve("twin_t_notch", {})

    def test_negative_freq(self):
        with pytest.raises(ValueError, match="positive"):
            solve("twin_t_notch", {"f_notch_hz": -100})


# ===========================================================================
# solver: Unknown topology
# ===========================================================================


def test_unknown_topology():
    with pytest.raises(ValueError, match="Unknown topology"):
        solve("mystery_circuit", {})


# ===========================================================================
# MCP tool integration
# ===========================================================================


class TestCalculateComponentsTool:
    def test_success(self):
        result = calculate_components("rc_lowpass_1st", {"f_cutoff_hz": 1000})
        assert result["status"] == "ok"
        assert "R1" in result["components"]

    def test_unknown_topology(self):
        result = calculate_components("unknown", {})
        assert result["status"] == "error"
        assert "Unknown topology" in result["error"]

    def test_missing_specs(self):
        result = calculate_components("rc_lowpass_1st", {})
        assert result["status"] == "error"
        assert "requires" in result["error"]


# ===========================================================================
# Helpers — parse formatted component values back to floats for verification
# ===========================================================================

_SUFFIXES = {
    "T": 1e12,
    "G": 1e9,
    "M": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


def _parse_eng(s: str) -> float:
    """Parse engineering-notation string back to float."""
    for suffix, mult in sorted(_SUFFIXES.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult
    return float(s)


def _parse_rc(result: dict) -> tuple[float, float]:
    c = result["components"]
    return _parse_eng(c["R1"]), _parse_eng(c["C1"])


def _parse_sallen_caps(result: dict) -> tuple[float, float]:
    c = result["components"]
    return _parse_eng(c["C1"]), _parse_eng(c["C2"])


def _parse_sallen_all(
    result: dict,
) -> tuple[float, float, float, float]:
    c = result["components"]
    return (
        _parse_eng(c["R1"]),
        _parse_eng(c["R2"]),
        _parse_eng(c["C1"]),
        _parse_eng(c["C2"]),
    )


def _parse_divider(result: dict) -> tuple[float, float]:
    c = result["components"]
    return _parse_eng(c["R1"]), _parse_eng(c["R2"])


def _parse_mfb(
    result: dict,
) -> tuple[float, float, float, float, float]:
    c = result["components"]
    return (
        _parse_eng(c["R1"]),
        _parse_eng(c["R2"]),
        _parse_eng(c["R3"]),
        _parse_eng(c["C1"]),
        _parse_eng(c["C2"]),
    )


def _parse_hpf_caps(result: dict) -> tuple[float, float]:
    c = result["components"]
    return _parse_eng(c["C1"]), _parse_eng(c["C2"])


def _parse_twin_t(
    result: dict,
) -> tuple[float, float, float, float]:
    c = result["components"]
    return (
        _parse_eng(c["R"]),
        _parse_eng(c["R_half"]),
        _parse_eng(c["C"]),
        _parse_eng(c["C_dbl"]),
    )
