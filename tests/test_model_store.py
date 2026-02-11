"""File I/O and ngspice integration tests for model_store."""

import json

import pytest

from spicebridge.model_generator import GeneratedModel, generate_model
from spicebridge.model_store import ModelStore

# ---------------------------------------------------------------------------
# File I/O tests (use tmp_path fixture)
# ---------------------------------------------------------------------------


class TestSave:
    def test_creates_lib(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        model = generate_model("diode", "D1N4148")
        path = store.save(model)
        assert path.exists()
        assert path.suffix == ".lib"

    def test_creates_index(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        model = generate_model("diode", "D1N4148")
        store.save(model)
        index_path = tmp_path / "index.json"
        assert index_path.exists()
        data = json.loads(index_path.read_text())
        assert "D1N4148" in data

    def test_lib_content_correct(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        model = generate_model("diode", "D1N4148")
        path = store.save(model)
        content = path.read_text()
        assert ".model D1N4148 D" in content

    def test_overwrite(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        m1 = generate_model("diode", "DTest", {"n": 1.0})
        m2 = generate_model("diode", "DTest", {"n": 2.0})
        store.save(m1)
        path = store.save(m2)
        content = path.read_text()
        assert "N=2.0" in content
        index = json.loads((tmp_path / "index.json").read_text())
        assert index["DTest"]["parameters"]["n"] == 2.0


class TestLoad:
    def test_returns_text_and_entry(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        model = generate_model("diode", "D1N4148")
        store.save(model)
        text, entry = store.load("D1N4148")
        assert ".model D1N4148 D" in text
        assert entry["component_type"] == "diode"

    def test_not_found_raises(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        with pytest.raises(KeyError, match="not found"):
            store.load("NoSuchModel")

    def test_missing_lib_file_raises(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        model = generate_model("diode", "DTest")
        store.save(model)
        # Remove the .lib file but leave the index
        (tmp_path / "DTest.lib").unlink()
        with pytest.raises(KeyError, match="missing"):
            store.load("DTest")


class TestListModels:
    def test_empty_store(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        assert store.list_models() == []

    def test_multiple_models(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        store.save(generate_model("diode", "D1N4148"))
        store.save(generate_model("bjt", "Q2N2222", {"type": "NPN"}))
        models = store.list_models()
        assert len(models) == 2
        names = [m["name"] for m in models]
        assert "D1N4148" in names
        assert "Q2N2222" in names

    def test_expected_keys(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        store.save(generate_model("diode", "DTest"))
        models = store.list_models()
        entry = models[0]
        assert "name" in entry
        assert "component_type" in entry
        assert "file_path" in entry
        assert "parameters" in entry

    def test_file_path_is_bare_filename(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        store.save(generate_model("diode", "DTest"))
        models = store.list_models()
        entry = models[0]
        assert "/" not in entry["file_path"]
        assert entry["file_path"] == "DTest.lib"


class TestDelete:
    def test_removes_file_and_index(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        model = generate_model("diode", "DTest")
        path = store.save(model)
        assert path.exists()
        store.delete("DTest")
        assert not path.exists()
        assert store.list_models() == []

    def test_not_found_raises(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        with pytest.raises(KeyError, match="not found"):
            store.delete("NoSuchModel")


class TestGetLibPath:
    def test_absolute_path(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        store.save(generate_model("diode", "DTest"))
        path = store.get_lib_path("DTest")
        assert path.is_absolute()

    def test_exists(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        store.save(generate_model("diode", "DTest"))
        path = store.get_lib_path("DTest")
        assert path.exists()

    def test_not_found(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        with pytest.raises(KeyError, match="not found"):
            store.get_lib_path("NoSuchModel")


class TestModelStoreNameValidation:
    """Verify model store re-validates names as defense-in-depth."""

    def test_save_rejects_traversal_name(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        model = GeneratedModel(
            name="../../etc/passwd",
            component_type="diode",
            spice_text=".model bad D (IS=1e-14)\n",
        )
        with pytest.raises(ValueError):
            store.save(model)

    def test_get_lib_path_rejects_traversal_name(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        with pytest.raises(ValueError):
            store.get_lib_path("../../etc/passwd")

    def test_save_rejects_empty_name(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        model = GeneratedModel(
            name="",
            component_type="diode",
            spice_text=".model bad D (IS=1e-14)\n",
        )
        with pytest.raises(ValueError):
            store.save(model)

    def test_get_lib_path_rejects_empty_name(self, tmp_path):
        store = ModelStore(base_dir=tmp_path)
        with pytest.raises(ValueError):
            store.get_lib_path("")


class TestDirectoryCreation:
    def test_nested_dir_created(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "models"
        store = ModelStore(base_dir=nested)
        model = generate_model("diode", "DTest")
        path = store.save(model)
        assert path.exists()
        assert nested.is_dir()


# ---------------------------------------------------------------------------
# Integration tests (require ngspice)
# ---------------------------------------------------------------------------


class TestIntegrationOpAmp:
    """Generate opamp → inverting amp → AC → verify gain."""

    def test_opamp_inverting_amp(self, tmp_path):
        from spicebridge.server import (
            _models,
            create_circuit,
            run_ac_analysis,
        )

        model = generate_model("opamp", "TestAmp")
        _models.save(model)

        netlist = (
            "Inverting Amplifier\n"
            "Vcc vcc 0 DC 15\n"
            "Vee vee 0 DC -15\n"
            "Vin in 0 AC 1\n"
            "R1 in inv_in 10k\n"
            "Rf inv_in out 100k\n"
            "XU1 0 inv_in out vcc vee TestAmp\n"
        )

        result = create_circuit(netlist, models=["TestAmp"])
        assert result["status"] == "ok"
        circuit_id = result["circuit_id"]

        ac = run_ac_analysis(circuit_id, start_freq=100, stop_freq=100000)
        assert ac["status"] == "ok"
        gain_db = ac["results"].get("gain_dc_dB")
        if gain_db is not None:
            assert abs(abs(gain_db) - 20) < 5, f"Expected ~20 dB, got {gain_db}"


class TestIntegrationBJT:
    """Generate NPN → common-emitter → DC OP → active region."""

    def test_bjt_common_emitter(self, tmp_path):
        from spicebridge.server import _models, create_circuit, run_dc_op

        model = generate_model("bjt", "QTest", {"type": "NPN"})
        _models.save(model)

        netlist = (
            "Common Emitter Amplifier\n"
            "Vcc vcc 0 DC 12\n"
            "Rc vcc col 4.7k\n"
            "Rb vcc base 470k\n"
            "Q1 col base 0 QTest\n"
        )

        result = create_circuit(netlist, models=["QTest"])
        assert result["status"] == "ok"
        circuit_id = result["circuit_id"]

        dc = run_dc_op(circuit_id)
        assert dc["status"] == "ok"
        nodes = dc["results"].get("nodes", {})
        v_col = None
        for k, v in nodes.items():
            if "col" in k.lower():
                v_col = v
                break
        if v_col is not None:
            assert 0 < v_col < 12, f"Collector voltage {v_col} not in active region"


class TestIntegrationDiode:
    """Generate diode → half-wave rectifier → transient."""

    def test_diode_half_wave(self, tmp_path):
        from spicebridge.server import (
            _models,
            create_circuit,
            run_transient,
        )

        model = generate_model("diode", "DTest")
        _models.save(model)

        netlist = (
            "Half Wave Rectifier\nVin in 0 SIN(0 5 1k)\nD1 in out DTest\nR1 out 0 1k\n"
        )

        result = create_circuit(netlist, models=["DTest"])
        assert result["status"] == "ok"
        circuit_id = result["circuit_id"]

        tran = run_transient(circuit_id, stop_time=5e-3, step_time=1e-6)
        assert tran["status"] == "ok"
        results = tran["results"]
        peak = results.get("peak_value")
        if peak is not None:
            assert peak > 3, f"Expected peak > 3V, got {peak}"
