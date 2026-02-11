"""Tests for spicebridge.prompt_translator."""

from __future__ import annotations

import json  # noqa: F401 — used in test_json_serializable

from spicebridge.prompt_translator import translate_prompt

# =========================================================================
# TestIntentClassification
# =========================================================================


class TestIntentClassification:
    """Test Stage 1 — intent classification via weighted keyword scoring."""

    def test_lowpass_filter_intent(self):
        r = translate_prompt("design a low-pass filter")
        assert r["intent"] == "design_filter"

    def test_highpass_filter_intent(self):
        r = translate_prompt("build a high-pass filter")
        assert r["intent"] == "design_filter"

    def test_bandpass_filter_intent(self):
        r = translate_prompt("I need a bandpass filter")
        assert r["intent"] == "design_filter"

    def test_notch_filter_intent(self):
        r = translate_prompt("create a notch filter at 60Hz")
        assert r["intent"] == "design_filter"

    def test_inverting_amplifier_intent(self):
        r = translate_prompt("inverting amplifier with 20dB gain")
        assert r["intent"] == "design_amplifier"

    def test_noninverting_amplifier_intent(self):
        r = translate_prompt("non-inverting amplifier")
        assert r["intent"] == "design_amplifier"

    def test_voltage_divider_intent(self):
        r = translate_prompt("voltage divider from 5V to 3.3V")
        assert r["intent"] == "design_amplifier"

    def test_buck_converter_intent(self):
        r = translate_prompt("design a buck converter")
        assert r["intent"] == "design_power"

    def test_boost_converter_intent(self):
        r = translate_prompt("boost converter 5V to 12V")
        assert r["intent"] == "design_power"

    def test_oscillator_intent(self):
        r = translate_prompt("Wien-bridge oscillator at 10kHz")
        assert r["intent"] == "design_oscillator"

    def test_modify_circuit_intent(self):
        r = translate_prompt("change R1 to 10kohm")
        assert r["intent"] == "modify_circuit"

    def test_analyze_circuit_intent(self):
        r = translate_prompt("analyze the frequency response")
        assert r["intent"] == "analyze_circuit"

    def test_general_question_fallback(self):
        r = translate_prompt("hello")
        assert r["intent"] == "general_question"
        assert r["confidence"] == 0.0

    def test_confidence_above_threshold_for_strong_match(self):
        r = translate_prompt("design a sallen-key butterworth low-pass filter")
        assert r["intent"] == "design_filter"
        assert r["confidence"] >= 0.6

    def test_confidence_single_keyword(self):
        r = translate_prompt("filter")
        assert r["intent"] == "design_filter"
        assert r["confidence"] > 0.0
        assert r["confidence"] <= 1.0

    def test_empty_string_is_general_question(self):
        r = translate_prompt("")
        assert r["intent"] == "general_question"
        assert r["confidence"] == 0.0


# =========================================================================
# TestParameterExtraction
# =========================================================================


class TestParameterExtraction:
    """Test Stage 2 — parameter extraction from text."""

    def test_khz_extraction(self):
        r = translate_prompt("low-pass filter at 1kHz")
        assert r["specs"].get("f_cutoff_hz") == 1000.0

    def test_mhz_extraction(self):
        r = translate_prompt("low-pass filter at 2.5MHz")
        assert r["specs"].get("f_cutoff_hz") == 2.5e6

    def test_hz_extraction(self):
        r = translate_prompt("low-pass filter at 500Hz")
        assert r["specs"].get("f_cutoff_hz") == 500.0

    def test_db_gain_extraction(self):
        r = translate_prompt("inverting amplifier 20dB gain")
        assert r["specs"].get("gain_dB") == 20.0

    def test_voltage_extraction_two_voltages(self):
        r = translate_prompt("voltage divider 5V to 3.3V")
        assert r["specs"].get("input_voltage") == 5.0
        assert r["specs"].get("output_voltage") == 3.3

    def test_kohm_extraction(self):
        r = translate_prompt("inverting amplifier 10kohm input impedance")
        assert r["specs"].get("input_impedance_ohms") == 10000.0

    def test_omega_extraction(self):
        r = translate_prompt("inverting amplifier 10kΩ input")
        assert r["specs"].get("input_impedance_ohms") == 10000.0

    def test_q_factor_equals(self):
        r = translate_prompt("bandpass filter 1kHz Q=1.5")
        assert r["specs"].get("Q") == 1.5

    def test_q_factor_of(self):
        r = translate_prompt("bandpass filter Q of 2.0")
        assert r["specs"].get("Q") == 2.0

    def test_ratio_extraction(self):
        r = translate_prompt("voltage divider ratio 0.5")
        assert r["specs"].get("ratio") == 0.5

    def test_num_inputs_extraction(self):
        r = translate_prompt("summing amplifier 4 inputs")
        assert r["specs"].get("num_inputs") == 4.0

    def test_gain_linear_no_unit(self):
        r = translate_prompt("differential amplifier gain of 5")
        assert r["specs"].get("gain_linear") == 5.0

    def test_gain_db_takes_precedence(self):
        r = translate_prompt("amplifier 20dB gain of 10")
        assert r["specs"].get("gain_dB") == 20.0
        assert "gain_linear" not in r["specs"]
        assert any("gain_dB" in w and "gain_linear" in w for w in r["warnings"])

    def test_missing_frequency_no_crash(self):
        r = translate_prompt("low-pass filter")
        assert "f_cutoff_hz" not in r["specs"]

    def test_ghz_extraction(self):
        r = translate_prompt("low-pass filter at 1.2GHz")
        assert r["specs"].get("f_cutoff_hz") == 1.2e9


# =========================================================================
# TestTemplateMatching
# =========================================================================


class TestTemplateMatching:
    """Test Stage 3 — template matching logic."""

    def test_rc_lowpass_1st(self):
        r = translate_prompt("simple low-pass filter at 1kHz")
        assert r["template_id"] == "rc_lowpass_1st"

    def test_sallen_key_lowpass_2nd(self):
        r = translate_prompt("2nd order low-pass filter at 1kHz")
        assert r["template_id"] == "sallen_key_lowpass_2nd"

    def test_butterworth_implies_2nd_order(self):
        r = translate_prompt("butterworth low-pass filter at 1kHz")
        assert r["template_id"] == "sallen_key_lowpass_2nd"

    def test_sallen_key_implies_2nd_order(self):
        r = translate_prompt("sallen-key lowpass at 1kHz")
        assert r["template_id"] == "sallen_key_lowpass_2nd"

    def test_q_implies_2nd_order(self):
        r = translate_prompt("low-pass filter 1kHz Q=0.707")
        assert r["template_id"] == "sallen_key_lowpass_2nd"

    def test_rc_highpass_1st(self):
        r = translate_prompt("high-pass filter at 500Hz")
        assert r["template_id"] == "rc_highpass_1st"

    def test_sallen_key_hpf_2nd(self):
        r = translate_prompt("2nd order high-pass filter at 500Hz")
        assert r["template_id"] == "sallen_key_hpf_2nd"

    def test_mfb_bandpass(self):
        r = translate_prompt("bandpass filter at 10kHz Q=2")
        assert r["template_id"] == "mfb_bandpass"

    def test_twin_t_notch(self):
        r = translate_prompt("notch filter at 60Hz")
        assert r["template_id"] == "twin_t_notch"

    def test_inverting_opamp(self):
        r = translate_prompt("inverting amplifier 20dB")
        assert r["template_id"] == "inverting_opamp"

    def test_noninverting_opamp(self):
        r = translate_prompt("non-inverting amplifier 10dB")
        assert r["template_id"] == "noninverting_opamp"
        assert r["has_template"] is False

    def test_summing_amplifier(self):
        r = translate_prompt("summing amplifier 4 inputs")
        assert r["template_id"] == "summing_amplifier"

    def test_differential_amp(self):
        r = translate_prompt("differential amplifier gain of 5")
        assert r["template_id"] == "differential_amp"

    def test_instrumentation_amp(self):
        r = translate_prompt("instrumentation amplifier gain of 100")
        assert r["template_id"] == "instrumentation_amp"

    def test_voltage_divider_template(self):
        r = translate_prompt("voltage divider 5V to 3.3V")
        assert r["template_id"] == "voltage_divider"

    def test_power_escalation(self):
        r = translate_prompt("build a buck converter")
        assert r["intent"] == "design_power"
        assert r["template_id"] is None

    def test_oscillator_escalation(self):
        r = translate_prompt("Colpitts oscillator")
        assert r["intent"] == "design_oscillator"
        assert r["template_id"] is None


# =========================================================================
# TestToolSequence
# =========================================================================


class TestToolSequence:
    """Test tool sequence generation."""

    def test_filter_sequence(self):
        r = translate_prompt("low-pass filter 1kHz")
        assert "load_template" in r["tool_sequence"]
        assert "run_ac_analysis" in r["tool_sequence"]
        assert "measure_bandwidth" in r["tool_sequence"]
        assert "draw_schematic" in r["tool_sequence"]

    def test_amplifier_sequence(self):
        r = translate_prompt("inverting amplifier 20dB")
        assert "load_template" in r["tool_sequence"]
        assert "run_dc_op" in r["tool_sequence"]
        assert "run_ac_analysis" in r["tool_sequence"]
        assert "measure_gain" in r["tool_sequence"]

    def test_voltage_divider_sequence(self):
        r = translate_prompt("voltage divider 5V to 3.3V")
        assert "run_dc_op" in r["tool_sequence"]
        assert "measure_dc" in r["tool_sequence"]
        assert "run_ac_analysis" not in r["tool_sequence"]

    def test_modify_sequence(self):
        r = translate_prompt("change R1 to 10kohm")
        assert r["tool_sequence"] == ["modify_component"]

    def test_escalate_sequence(self):
        r = translate_prompt("build a boost converter")
        assert r["tool_sequence"] == ["escalate"]

    def test_general_question_empty_sequence(self):
        r = translate_prompt("hello there")
        assert r["tool_sequence"] == []


# =========================================================================
# TestFormattedPrompt
# =========================================================================


class TestFormattedPrompt:
    """Test formatted prompt generation."""

    def test_auto_design_format(self):
        r = translate_prompt("low-pass filter 1kHz 10dB")
        fp = r["formatted_prompt"]
        assert "auto_design" in fp
        assert "rc_lowpass_1st" in fp
        assert "sim_type" in fp

    def test_escalate_format(self):
        r = translate_prompt("buck converter 12V")
        fp = r["formatted_prompt"]
        assert "[ESCALATE TO CLOUD]" in fp
        assert "buck converter" in fp

    def test_solver_only_format(self):
        r = translate_prompt("non-inverting amplifier 10dB")
        fp = r["formatted_prompt"]
        assert "calculate_components" in fp
        assert "noninverting_opamp" in fp

    def test_missing_specs_noted(self):
        r = translate_prompt("low-pass filter")
        fp = r["formatted_prompt"]
        assert "missing required specs" in fp
        assert "f_cutoff_hz" in fp

    def test_original_input_always_present(self):
        r = translate_prompt("some random text")
        assert "some random text" in r["formatted_prompt"]


# =========================================================================
# TestEdgeCases
# =========================================================================


class TestEdgeCases:
    """Test edge cases and robustness."""

    def test_empty_string(self):
        r = translate_prompt("")
        assert r["intent"] == "general_question"
        assert r["confidence"] == 0.0
        assert r["template_id"] is None
        assert r["tool_sequence"] == []

    def test_contradictory_gain_warning(self):
        r = translate_prompt("amplifier 20dB gain of 10")
        assert len(r["warnings"]) > 0

    def test_case_insensitivity(self):
        r1 = translate_prompt("LOW-PASS FILTER 1KHZ")
        r2 = translate_prompt("low-pass filter 1kHz")
        assert r1["intent"] == r2["intent"]
        assert r1["template_id"] == r2["template_id"]

    def test_all_keys_present(self):
        r = translate_prompt("design a filter")
        expected_keys = {
            "intent",
            "confidence",
            "specs",
            "template_id",
            "has_template",
            "sim_type",
            "tool_sequence",
            "formatted_prompt",
            "missing_required",
            "warnings",
            "original_input",
        }
        assert set(r.keys()) == expected_keys

    def test_original_input_preserved(self):
        text = "design a 1kHz low-pass filter with 10dB gain"
        r = translate_prompt(text)
        assert r["original_input"] == text

    def test_json_serializable(self):
        r = translate_prompt("inverting amplifier 20dB gain 10kohm input")
        # Should not raise
        serialized = json.dumps(r)
        assert isinstance(serialized, str)

    def test_bandpass_freq_mapped_to_f_center_hz(self):
        r = translate_prompt("bandpass filter at 10kHz")
        assert "f_center_hz" in r["specs"]
        assert r["specs"]["f_center_hz"] == 10000.0

    def test_notch_freq_mapped_to_f_notch_hz(self):
        r = translate_prompt("notch filter at 60Hz")
        assert "f_notch_hz" in r["specs"]
        assert r["specs"]["f_notch_hz"] == 60.0

    def test_no_internal_freq_key_leaked(self):
        r = translate_prompt("low-pass filter at 1kHz")
        assert "_freq_hz" not in r["specs"]

    def test_buffer_maps_to_noninverting(self):
        r = translate_prompt("unity gain buffer")
        assert r["template_id"] == "noninverting_opamp"
