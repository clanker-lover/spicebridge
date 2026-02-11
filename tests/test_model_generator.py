"""Unit tests for model_generator — pure logic, no I/O, no ngspice."""

import math

import pytest

from spicebridge.model_generator import (
    GeneratedModel,
    generate_model,
    get_default_parameters,
    list_component_types,
)

# ---------------------------------------------------------------------------
# Dispatch & component types
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_list_component_types(self):
        types = list_component_types()
        assert types == ["bjt", "diode", "mosfet", "opamp"]

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown component type"):
            generate_model("resistor", "R1")

    def test_get_default_parameters_opamp(self):
        defaults = get_default_parameters("opamp")
        assert "gbw_hz" in defaults
        assert defaults["gbw_hz"] == 10e6

    def test_get_default_parameters_unknown(self):
        with pytest.raises(ValueError, match="Unknown component type"):
            get_default_parameters("resistor")


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------


class TestNameValidation:
    def test_empty_name(self):
        with pytest.raises(ValueError, match="must not be empty"):
            generate_model("opamp", "")

    def test_digit_start(self):
        with pytest.raises(ValueError, match="must start with a letter"):
            generate_model("opamp", "2N2222")

    def test_special_chars(self):
        with pytest.raises(ValueError, match="must start with a letter"):
            generate_model("opamp", "OPA-2134")

    def test_valid_names(self):
        for name in ("OPA2134", "Q2N2222A", "my_model", "M1"):
            model = generate_model("diode", name)
            assert model.name == name


# ---------------------------------------------------------------------------
# Op-Amp
# ---------------------------------------------------------------------------


class TestOpAmp:
    def test_default_params(self):
        model = generate_model("opamp", "TestOpAmp")
        assert isinstance(model, GeneratedModel)
        assert model.component_type == "opamp"
        assert model.parameters["gbw_hz"] == 10e6

    def test_subckt_structure(self):
        model = generate_model("opamp", "TestOpAmp")
        assert ".subckt TestOpAmp inp inn out vcc vee" in model.spice_text
        assert ".ends TestOpAmp" in model.spice_text

    def test_stage_elements_present(self):
        model = generate_model("opamp", "TestOpAmp")
        text = model.spice_text
        assert "Rin inp inn" in text
        assert "Egain mid 0 VALUE=" in text
        assert "Gslew 0 int VALUE=" in text
        assert "Cpole int 0" in text
        assert "Bclamp clamped 0 V=" in text
        assert "Rout clamped out" in text
        assert "Isupp vcc vee DC" in text

    def test_gain_calculation(self):
        model = generate_model("opamp", "TestOpAmp", {"dc_gain_db": 80})
        adc = 10 ** (80 / 20)
        assert model.metadata["calculated"]["Adc"] == pytest.approx(adc)

    def test_pole_frequency(self):
        model = generate_model("opamp", "TestOpAmp")
        defaults = get_default_parameters("opamp")
        adc = 10 ** (defaults["dc_gain_db"] / 20)
        expected_fpole = defaults["gbw_hz"] / adc
        actual = model.metadata["calculated"]["f_pole"]
        assert actual == pytest.approx(expected_fpole)

    def test_rc_product(self):
        model = generate_model("opamp", "TestOpAmp")
        calc = model.metadata["calculated"]
        expected_cpole = 1 / (2 * math.pi * calc["f_pole"] * calc["Rpole"])
        assert calc["Cpole"] == pytest.approx(expected_cpole)

    def test_custom_params(self):
        model = generate_model(
            "opamp",
            "CustomAmp",
            {
                "gbw_hz": 1e6,
                "dc_gain_db": 60,
                "output_impedance_ohm": 100,
            },
        )
        assert model.parameters["gbw_hz"] == 1e6
        assert model.parameters["dc_gain_db"] == 60
        assert model.parameters["output_impedance_ohm"] == 100
        assert "Rout clamped out 100" in model.spice_text

    def test_single_line_expressions(self):
        """ngspice requires behavioural expressions on single lines."""
        model = generate_model("opamp", "TestOpAmp")
        for line in model.spice_text.splitlines():
            if line.startswith(("Egain", "Gslew", "Bclamp")):
                assert "\n" not in line

    def test_notes_mention_psrr(self):
        model = generate_model("opamp", "TestOpAmp")
        assert any("PSRR" in n for n in model.notes)

    def test_notes_mention_drift(self):
        model = generate_model("opamp", "TestOpAmp")
        assert any("drift" in n.lower() for n in model.notes)


# ---------------------------------------------------------------------------
# BJT
# ---------------------------------------------------------------------------


class TestBJT:
    def test_npn_defaults(self):
        model = generate_model("bjt", "Q2N2222", {"type": "NPN"})
        assert model.component_type == "bjt"
        assert ".model Q2N2222 NPN" in model.spice_text
        assert "BF=200" in model.spice_text

    def test_pnp(self):
        model = generate_model("bjt", "Q2N2907", {"type": "PNP"})
        assert ".model Q2N2907 PNP" in model.spice_text

    def test_custom_params(self):
        model = generate_model(
            "bjt", "QCustom", {"type": "NPN", "bf": 300, "vaf_v": 50}
        )
        assert "BF=300" in model.spice_text
        assert "VAF=50" in model.spice_text

    def test_invalid_type(self):
        with pytest.raises(ValueError, match="NPN or PNP"):
            generate_model("bjt", "QBad", {"type": "FET"})

    def test_capacitance_suffix_p(self):
        model = generate_model("bjt", "QTest", {"type": "NPN", "cje_pf": 10})
        assert "CJE=10p" in model.spice_text

    def test_transit_time_suffix_n(self):
        model = generate_model("bjt", "QTest", {"type": "NPN", "tf_ns": 0.5})
        assert "TF=0.5n" in model.spice_text


# ---------------------------------------------------------------------------
# MOSFET
# ---------------------------------------------------------------------------


class TestMOSFET:
    def test_nmos_defaults(self):
        model = generate_model("mosfet", "MTest", {"type": "NMOS"})
        assert ".model MTest NMOS" in model.spice_text
        assert "VTO=1.5" in model.spice_text

    def test_pmos_defaults(self):
        model = generate_model("mosfet", "MPTest", {"type": "PMOS"})
        assert ".model MPTest PMOS" in model.spice_text
        assert "VTO=-1.5" in model.spice_text

    def test_pmos_vth_negative(self):
        """PMOS default Vth should be negative."""
        model = generate_model("mosfet", "MPTest", {"type": "PMOS"})
        assert "VTO=-1.5" in model.spice_text

    def test_pmos_user_override_vth(self):
        """If user explicitly supplies vth_v, use as-is."""
        model = generate_model("mosfet", "MPTest", {"type": "PMOS", "vth_v": -2.0})
        assert "VTO=-2.0" in model.spice_text

    def test_wl_in_metadata_not_model(self):
        model = generate_model("mosfet", "MTest", {"type": "NMOS"})
        assert "W=" not in model.spice_text
        assert "L=" not in model.spice_text
        assert "instance_params" in model.metadata
        assert "W" in model.metadata["instance_params"]
        assert "L" in model.metadata["instance_params"]

    def test_invalid_type(self):
        with pytest.raises(ValueError, match="NMOS or PMOS"):
            generate_model("mosfet", "MBad", {"type": "JFET"})

    def test_kp_suffix_u(self):
        model = generate_model("mosfet", "MTest", {"type": "NMOS", "kp_ua_v2": 400})
        assert "KP=400u" in model.spice_text


# ---------------------------------------------------------------------------
# Diode
# ---------------------------------------------------------------------------


class TestDiode:
    def test_defaults(self):
        model = generate_model("diode", "D1N4148")
        assert ".model D1N4148 D" in model.spice_text
        assert "N=1.05" in model.spice_text

    def test_custom_params(self):
        model = generate_model("diode", "DCustom", {"bv_v": 50, "n": 1.8})
        assert "BV=50" in model.spice_text
        assert "N=1.8" in model.spice_text

    def test_is_scientific_notation(self):
        model = generate_model("diode", "DTest")
        assert "IS=1.0000e-14" in model.spice_text

    def test_cjo_suffix_p(self):
        model = generate_model("diode", "DTest", {"cjo_pf": 10})
        assert "CJO=10p" in model.spice_text

    def test_tt_suffix_n(self):
        model = generate_model("diode", "DTest", {"tt_ns": 8})
        assert "TT=8n" in model.spice_text
