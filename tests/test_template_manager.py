"""Unit tests for spicebridge.template_manager."""

import json
import textwrap
from unittest.mock import patch

import pytest

from spicebridge.template_manager import (
    TemplateManager,
    modify_component_in_netlist,
    substitute_params,
)

# --- substitute_params tests ---


def test_substitute_params_single_key():
    netlist = textwrap.dedent("""\
        * Test
        .param R1=10k
        R1 in out {R1}""")
    result = substitute_params(netlist, {"R1": "22k"})
    assert ".param R1=22k" in result
    # Component reference line stays untouched
    assert "R1 in out {R1}" in result


def test_substitute_params_multiple_keys():
    netlist = ".param R1=10k\n.param C1=10n"
    result = substitute_params(netlist, {"R1": "47k", "C1": "100n"})
    assert ".param R1=47k" in result
    assert ".param C1=100n" in result


def test_substitute_params_empty_dict():
    netlist = ".param R1=10k"
    result = substitute_params(netlist, {})
    assert result == netlist


def test_substitute_params_nonexistent_key():
    netlist = ".param R1=10k"
    result = substitute_params(netlist, {"R99": "1M"})
    assert ".param R1=10k" in result


# --- modify_component_in_netlist tests ---


def test_modify_param_line():
    netlist = "* Test\n.param R1=10k\nR1 in out {R1}"
    result = modify_component_in_netlist(netlist, "R1", "47k")
    assert ".param R1=47k" in result


def test_modify_instance_line():
    netlist = "* Test\nR1 in out 1k\nR2 out 0 2k"
    result = modify_component_in_netlist(netlist, "R2", "5k")
    assert "5k" in result
    # R1 should be unchanged
    assert "R1 in out 1k" in result


def test_modify_component_not_found():
    netlist = "* Test\nR1 in out 1k"
    with pytest.raises(ValueError, match="not found"):
        modify_component_in_netlist(netlist, "C99", "100n")


# --- TemplateManager tests ---


def test_list_all_templates():
    mgr = TemplateManager()
    templates = mgr.list_templates()
    assert len(templates) >= 5


def test_list_filter_by_category():
    mgr = TemplateManager()
    filters = mgr.list_templates(category="filters")
    assert len(filters) == 6
    assert all(t["category"] == "filters" for t in filters)


def test_list_source_flag():
    mgr = TemplateManager()
    templates = mgr.list_templates()
    assert all(t["source"] == "built-in" for t in templates)


def test_get_valid_template():
    mgr = TemplateManager()
    t = mgr.get_template("rc_lowpass_1st")
    assert t.id == "rc_lowpass_1st"
    assert t.category == "filters"
    assert ".param R1=" in t.netlist


def test_get_invalid_template():
    mgr = TemplateManager()
    with pytest.raises(KeyError, match="not found"):
        mgr.get_template("nonexistent_template")


def test_user_override(tmp_path):
    """A user template with the same id as a built-in should override it."""
    user_template = {
        "id": "voltage_divider",
        "name": "My Custom Divider",
        "category": "basic",
        "description": "User override",
        "netlist": "* custom\nR1 in out 1k\nR2 out 0 1k",
        "components": {},
    }
    user_dir = tmp_path / "templates"
    user_dir.mkdir()
    (user_dir / "voltage_divider.json").write_text(json.dumps(user_template))

    mgr = TemplateManager()
    with patch.object(TemplateManager, "_user_dir", return_value=user_dir):
        mgr.reload()
        t = mgr.get_template("voltage_divider")
        assert t.source == "user"
        assert t.name == "My Custom Divider"


def test_user_dir_creation(tmp_path):
    """_user_dir should create the directory if it doesn't exist."""
    fake_home = tmp_path / "fakehome"
    with patch("spicebridge.template_manager.Path.home", return_value=fake_home):
        d = TemplateManager._user_dir()
        assert d.is_dir()
        assert d == fake_home / ".spicebridge" / "templates"
