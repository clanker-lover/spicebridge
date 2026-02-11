"""Tests for auto-injecting .include lines via the models parameter."""

import pytest

from spicebridge.model_generator import generate_model
from spicebridge.model_store import ModelStore
from spicebridge.server import _manager, create_circuit, load_template

SIMPLE_NETLIST = """\
Test Circuit
V1 in 0 AC 1
R1 in out 1k
C1 out 0 100n
.end
"""


@pytest.fixture()
def model_store(tmp_path, monkeypatch):
    """Create a temporary ModelStore and patch the server global."""
    store = ModelStore(base_dir=tmp_path)
    monkeypatch.setattr("spicebridge.server._models", store)
    return store


def _save_test_model(store, name="TestModel"):
    """Helper to save a diode model under the given name."""
    model = generate_model("diode", name)
    store.save(model)
    return name


# ---------------------------------------------------------------------------
# create_circuit tests
# ---------------------------------------------------------------------------


class TestCreateCircuitWithModels:
    def test_create_circuit_with_model(self, model_store):
        name = _save_test_model(model_store)
        result = create_circuit(SIMPLE_NETLIST, models=[name])
        assert result["status"] == "ok"
        stored = _manager.get(result["circuit_id"]).netlist
        assert ".include" in stored
        assert name in stored

    def test_create_circuit_model_not_found(self, model_store):
        result = create_circuit(SIMPLE_NETLIST, models=["NonExistent"])
        assert result["status"] == "error"
        assert "NonExistent" in result["error"]
        assert "not found" in result["error"]

    def test_create_circuit_no_models_default(self, model_store):
        result = create_circuit(SIMPLE_NETLIST)
        assert result["status"] == "ok"
        stored = _manager.get(result["circuit_id"]).netlist
        assert ".include" not in stored


# ---------------------------------------------------------------------------
# load_template tests
# ---------------------------------------------------------------------------


class TestLoadTemplateWithModels:
    def test_load_template_with_model(self, model_store):
        name = _save_test_model(model_store)
        result = load_template("rc_lowpass_1st", models=[name])
        assert result["status"] == "ok"
        stored = _manager.get(result["circuit_id"]).netlist
        assert ".include" in stored
        assert name in stored

    def test_load_template_model_not_found(self, model_store):
        result = load_template("rc_lowpass_1st", models=["NonExistent"])
        assert result["status"] == "error"
        assert "NonExistent" in result["error"]
        assert "not found" in result["error"]

    def test_load_template_no_models_default(self, model_store):
        result = load_template("rc_lowpass_1st")
        assert result["status"] == "ok"
        stored = _manager.get(result["circuit_id"]).netlist
        assert ".include" not in stored


# ---------------------------------------------------------------------------
# Multiple models
# ---------------------------------------------------------------------------


class TestMultipleModels:
    def test_multiple_models(self, model_store):
        name1 = _save_test_model(model_store, "ModelA")
        name2 = _save_test_model(model_store, "ModelB")
        result = create_circuit(SIMPLE_NETLIST, models=[name1, name2])
        assert result["status"] == "ok"
        stored = _manager.get(result["circuit_id"]).netlist
        assert "ModelA" in stored
        assert "ModelB" in stored
        # Both .include lines should be present
        include_lines = [
            line for line in stored.splitlines() if line.startswith(".include")
        ]
        assert len(include_lines) == 2
