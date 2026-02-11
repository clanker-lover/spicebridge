"""Security regression tests for SPICEBridge.

Integration-level tests that verify security boundaries hold across
MCP tool entry points, source-code invariants, and web viewer hardening.
Unit-level sanitization is covered in test_sanitize.py.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from spicebridge.circuit_manager import CircuitManager
from spicebridge.model_generator import generate_model
from spicebridge.sanitize import validate_include_paths
from spicebridge.server import (
    connect_stages,
    create_circuit,
    draw_schematic,
    export_kicad,
    modify_component,
    run_ac_analysis,
    run_monte_carlo,
    set_ports,
)
from spicebridge.simulator import validate_netlist_syntax
from spicebridge.template_manager import TemplateManager
from spicebridge.web_viewer import _ViewerServer

# ---------------------------------------------------------------------------
# Reusable payloads
# ---------------------------------------------------------------------------

_CLEAN_NETLIST = """\
* RC Low-Pass Filter
V1 in 0 AC 1
R1 in out 1k
C1 out 0 100n
.end
"""

_SYSTEM_DIRECTIVE_NETLIST = """\
Test
.system echo PWNED
.end
"""

_CONTROL_BLOCK_NETLIST = """\
Test
.control
shell echo PWNED
.endc
.end
"""

_INCLUDE_SENSITIVE_NETLIST = """\
Test
.include /etc/shadow
.end
"""

_BACKTICK_NETLIST = """\
Test
R1 1 2 `echo 1k`
.end
"""

# ---------------------------------------------------------------------------
# Helpers for AST inspection
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "spicebridge"
_SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}


def _is_subprocess_call(node: ast.Call) -> bool:
    """Check if an AST Call node is a subprocess.xxx() call."""
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr in _SUBPROCESS_FUNCS
    )


def _iter_subprocess_calls():
    """Yield (path, ast.Call) for every subprocess call in the source tree."""
    for py_file in sorted(_SRC_DIR.glob("**/*.py")):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_subprocess_call(node):
                yield py_file, node


# ---------------------------------------------------------------------------
# Fixtures for web viewer tests
# ---------------------------------------------------------------------------


@pytest.fixture
def manager():
    return CircuitManager()


@pytest.fixture
def viewer_app(manager):
    server = _ViewerServer(manager, "127.0.0.1", 0)
    return server._build_app()


@pytest.fixture
async def cli(aiohttp_client, viewer_app):
    return await aiohttp_client(viewer_app)


# ===========================================================================
# Class 1: Subprocess Safety (static AST checks)
# ===========================================================================


class TestSubprocessSafety:
    """Static AST inspection of source files for subprocess misuse."""

    def test_no_shell_true_in_source(self):
        for py_file, call_node in _iter_subprocess_calls():
            for kw in call_node.keywords:
                if kw.arg == "shell":
                    assert not (
                        isinstance(kw.value, ast.Constant) and kw.value.value is True
                    ), f"{py_file.name}:{call_node.lineno} uses shell=True"

    def test_subprocess_uses_list_args(self):
        for py_file, call_node in _iter_subprocess_calls():
            assert call_node.args, (
                f"{py_file.name}:{call_node.lineno} "
                f"subprocess call has no positional args"
            )
            first_arg = call_node.args[0]
            assert isinstance(first_arg, ast.List), (
                f"{py_file.name}:{call_node.lineno} first arg is "
                f"{type(first_arg).__name__}, expected List"
            )

    def test_subprocess_has_timeout(self):
        for py_file, call_node in _iter_subprocess_calls():
            kw_names = {kw.arg for kw in call_node.keywords}
            assert "timeout" in kw_names, (
                f"{py_file.name}:{call_node.lineno} subprocess call missing timeout"
            )

    def test_only_simulator_imports_subprocess(self):
        for py_file in sorted(_SRC_DIR.glob("**/*.py")):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "subprocess":
                            assert py_file.name == "simulator.py", (
                                f"{py_file.name} imports subprocess"
                            )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and "subprocess" in node.module
                ):
                    assert py_file.name == "simulator.py", (
                        f"{py_file.name} imports from subprocess"
                    )


# ===========================================================================
# Class 2: Netlist Injection End-to-End
# ===========================================================================


class TestNetlistInjectionEndToEnd:
    """Verify MCP create_circuit rejects adversarial netlists."""

    @pytest.mark.parametrize(
        ("netlist", "match"),
        [
            (_SYSTEM_DIRECTIVE_NETLIST, "not allowed"),
            (_CONTROL_BLOCK_NETLIST, "not allowed"),
            (_INCLUDE_SENSITIVE_NETLIST, "not allowed"),
            (_BACKTICK_NETLIST, "Backtick"),
        ],
        ids=["system", "control", "include", "backtick"],
    )
    def test_create_circuit_rejects_malicious_netlist(self, netlist, match):
        result = create_circuit(netlist)
        assert result["status"] == "error"
        assert match in result["error"]

    def test_create_circuit_rejects_oversized_netlist(self):
        huge = "* title\n" + "R1 1 2 1k\n" * 200_000
        assert len(huge) > 1_000_000
        result = create_circuit(huge)
        assert result["status"] == "error"
        assert "too large" in result["error"]


# ===========================================================================
# Class 3: Component Value Injection
# ===========================================================================


class TestComponentValueInjection:
    """Verify modify_component rejects adversarial component values."""

    @pytest.mark.parametrize(
        ("value", "match"),
        [
            ("1k\n.system echo pwned", "newline"),
            ("1k; echo pwned", "semicolon"),
            ("`cat /etc/passwd`", "backtick"),
            (".system echo pwned", "directive"),
            ("1k$PATH", "disallowed"),
        ],
        ids=["newline", "semicolon", "backtick", "directive", "dollar"],
    )
    def test_modify_component_rejects_malicious_value(self, value, match):
        setup = create_circuit(_CLEAN_NETLIST)
        assert setup["status"] == "ok"
        cid = setup["circuit_id"]
        result = modify_component(cid, "R1", value)
        assert result["status"] == "error"
        assert re.search(match, result["error"], re.IGNORECASE), (
            f"Expected '{match}' in error: {result['error']}"
        )


# ===========================================================================
# Class 4: Path Traversal
# ===========================================================================


class TestPathTraversal:
    """Verify path traversal is blocked across tool boundaries."""

    @pytest.mark.parametrize(
        "filename",
        [
            "../../etc/passwd",
            "..\\..\\etc\\passwd",
            "../secret",
            "/etc/passwd",
        ],
        ids=["dot-dot-slash", "backslash", "relative", "absolute"],
    )
    def test_export_kicad_rejects_traversal(self, filename):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        result = export_kicad(cid, filename=filename)
        assert result["status"] == "error"

    @pytest.mark.parametrize(
        "fmt",
        ["../../../etc/passwd", "exe"],
        ids=["traversal", "disallowed-ext"],
    )
    def test_draw_schematic_rejects_invalid_format(self, fmt):
        result = draw_schematic("any_id", fmt=fmt)
        assert result["status"] == "error"
        assert "Invalid format" in result["error"]

    def test_circuit_id_is_safe_hex(self):
        pattern = re.compile(r"^[0-9a-f]{8}$")
        for _ in range(50):
            result = create_circuit(_CLEAN_NETLIST)
            assert result["status"] == "ok"
            cid = result["circuit_id"]
            assert pattern.match(cid), f"Circuit ID '{cid}' is not safe hex"


# ===========================================================================
# Class 5: Resource Limits
# ===========================================================================


class TestResourceLimits:
    """Verify resource bounds are enforced on MCP tools."""

    @pytest.mark.parametrize("num_runs", [0, -1, 1001, 10_000])
    def test_monte_carlo_rejects_out_of_range(self, num_runs):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        result = run_monte_carlo(cid, analysis_type="ac", num_runs=num_runs)
        assert result["status"] == "error"
        assert "between 1 and 1000" in result["error"]

    @pytest.mark.parametrize("num_runs", [1, 1000])
    def test_monte_carlo_accepts_boundary_values(self, num_runs):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        result = run_monte_carlo(cid, analysis_type="ac", num_runs=num_runs)
        # May fail for other reasons (no ngspice), but not for bounds
        if result["status"] == "error":
            assert "between 1 and 1000" not in result["error"]

    def test_simulation_timeout_handled(self):
        with (
            patch("spicebridge.simulator.subprocess.run") as mock_run,
            patch("spicebridge.simulator._check_ngspice", return_value=True),
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["ngspice"], timeout=10
            )
            valid, errors = validate_netlist_syntax("* test\n.end\n")
            assert valid is False
            assert errors == ["ngspice timed out"]


# ===========================================================================
# Class 6: Web Viewer Security
# ===========================================================================


class TestWebViewerSecurity:
    """Verify security hardening of the aiohttp web viewer."""

    @pytest.mark.asyncio
    async def test_security_headers_on_index(self, cli):
        resp = await cli.get("/")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in resp.headers
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    @pytest.mark.asyncio
    async def test_security_headers_on_api(self, cli):
        resp = await cli.get("/api/circuits")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in resp.headers
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    @pytest.mark.asyncio
    async def test_websocket_rejects_foreign_origin(self, cli):
        resp = await cli.get("/ws", headers={"Origin": "http://evil.com"})
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_websocket_accepts_localhost_origin(self, cli):
        ws = await cli.ws_connect("/ws", headers={"Origin": "http://localhost"})
        await ws.close()

    @pytest.mark.asyncio
    async def test_websocket_accepts_127_origin(self, cli):
        ws = await cli.ws_connect("/ws", headers={"Origin": "http://127.0.0.1"})
        await ws.close()

    @pytest.mark.asyncio
    async def test_websocket_accepts_no_origin(self, cli):
        ws = await cli.ws_connect("/ws")
        await ws.close()

    @pytest.mark.asyncio
    async def test_no_directory_traversal_via_url(self, cli):
        for path in ["/../../etc/passwd", "/static/../secret"]:
            resp = await cli.get(path)
            assert resp.status == 404, f"Expected 404 for {path}, got {resp.status}"


# ===========================================================================
# Class 7: Circuit ID Safety
# ===========================================================================


class TestCircuitIdSafety:
    """Verify fabricated/malicious circuit IDs produce errors, not crashes."""

    def test_path_separator_in_circuit_id(self):
        result = run_monte_carlo("../../etc", analysis_type="ac", num_runs=10)
        assert result["status"] == "error"

    def test_null_bytes_in_circuit_id(self):
        result = run_monte_carlo("abc\x00def", analysis_type="ac", num_runs=10)
        assert result["status"] == "error"

    def test_nonexistent_id_returns_error(self):
        result = run_monte_carlo("deadbeef", analysis_type="ac", num_runs=10)
        assert result["status"] == "error"


# ===========================================================================
# Class 8: set_ports Validation
# ===========================================================================


class TestSetPortsValidation:
    """Verify set_ports rejects adversarial port/node names."""

    def test_rejects_port_name_with_newline(self):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        result = set_ports(cid, {"in\n.system pwned": "node1"})
        assert result["status"] == "error"
        assert "Invalid port name" in result["error"]

    def test_rejects_port_name_with_spaces(self):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        result = set_ports(cid, {"port name": "node1"})
        assert result["status"] == "error"
        assert "Invalid port name" in result["error"]

    def test_rejects_node_name_with_newline(self):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        result = set_ports(cid, {"in": "node\n.shell cmd"})
        assert result["status"] == "error"
        assert "Invalid node name" in result["error"]

    def test_accepts_valid_spice_names(self):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        result = set_ports(cid, {"input": "in", "out_1": "out", "VCC": "vdd"})
        assert result["status"] == "ok"


# ===========================================================================
# Class 9: connect_stages Validation
# ===========================================================================


class TestConnectStagesValidation:
    """Verify connect_stages rejects adversarial stage labels."""

    def test_rejects_label_with_newline(self):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        set_ports(cid, {"in": "in", "out": "out", "gnd": "0"})
        result = connect_stages([{"circuit_id": cid, "label": "stage\n.system pwned"}])
        assert result["status"] == "error"
        assert "Invalid stage label" in result["error"]

    def test_rejects_label_with_special_chars(self):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        set_ports(cid, {"in": "in", "out": "out", "gnd": "0"})
        result = connect_stages([{"circuit_id": cid, "label": "stage;pwned"}])
        assert result["status"] == "error"
        assert "Invalid stage label" in result["error"]

    def test_accepts_clean_labels(self):
        setup = create_circuit(_CLEAN_NETLIST)
        cid = setup["circuit_id"]
        set_ports(cid, {"in": "in", "out": "out", "gnd": "0"})
        # Labels like "preamp", "S1", "output_stage" should not trigger
        # a label validation error (may fail for other reasons)
        for label in ["preamp", "S1", "output_stage"]:
            result = connect_stages([{"circuit_id": cid, "label": label}])
            if result["status"] == "error":
                assert "Invalid stage label" not in result["error"]


# ===========================================================================
# Class 10: Model Type Injection
# ===========================================================================


class TestModelTypeInjection:
    """Verify model generators reject injected type strings."""

    def test_bjt_rejects_injected_type(self):
        with pytest.raises(ValueError, match="BJT type must be NPN or PNP"):
            generate_model("bjt", "Q1", {"type": "NPN\n.system pwned"})

    @pytest.mark.parametrize("bjt_type", ["NPN", "PNP", "npn", "pnp"])
    def test_bjt_accepts_valid_types(self, bjt_type):
        model = generate_model("bjt", "Q1", {"type": bjt_type})
        assert model.component_type == "bjt"

    def test_mosfet_rejects_injected_type(self):
        with pytest.raises(ValueError, match="MOSFET type must be NMOS or PMOS"):
            generate_model("mosfet", "M1", {"type": "NMOS\n.shell cmd"})

    @pytest.mark.parametrize("mos_type", ["NMOS", "PMOS", "nmos", "pmos"])
    def test_mosfet_accepts_valid_types(self, mos_type):
        model = generate_model("mosfet", "M1", {"type": mos_type})
        assert model.component_type == "mosfet"


# ===========================================================================
# Class 11: Model Parameter Injection
# ===========================================================================


class TestModelParameterInjection:
    """Verify model generators reject non-numeric parameter values."""

    def test_bjt_rejects_string_parameter(self):
        with pytest.raises(ValueError, match="Invalid model parameter value"):
            generate_model(
                "bjt", "Q1", {"bf": "200) ; .control\nshell rm -rf /\n.endc"}
            )

    def test_diode_rejects_string_parameter(self):
        with pytest.raises(ValueError, match="Invalid model parameter value"):
            generate_model(
                "diode", "D1", {"n": "1.05) ; .control\nshell rm -rf /\n.endc"}
            )

    def test_mosfet_rejects_string_parameter(self):
        with pytest.raises(ValueError, match="Invalid model parameter value"):
            generate_model("mosfet", "M1", {"kp_ua_v2": "200u\n.system pwned"})

    def test_opamp_rejects_string_parameter(self):
        with pytest.raises(ValueError, match="Invalid model parameter value"):
            generate_model("opamp", "U1", {"dc_gain_db": "100\n.system echo pwned"})


# ===========================================================================
# Class 12: Include Path Validation
# ===========================================================================


class TestIncludePathValidation:
    """Verify validate_include_paths blocks directory escape."""

    def test_rejects_etc_passwd(self, tmp_path):
        netlist = "* title\n.include /etc/passwd\n.end\n"
        with pytest.raises(ValueError, match="resolves outside allowed directories"):
            validate_include_paths(netlist, [tmp_path])

    def test_accepts_valid_models_dir(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        netlist = f"* title\n.include {models_dir / 'foo.lib'}\n.end\n"
        # Should not raise
        validate_include_paths(netlist, [models_dir])

    def test_rejects_traversal(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        netlist = f"* title\n.include {models_dir / '../../etc/passwd'}\n.end\n"
        with pytest.raises(ValueError, match="resolves outside allowed directories"):
            validate_include_paths(netlist, [models_dir])

    def test_rejects_lib_directive(self, tmp_path):
        netlist = "* title\n.lib /etc/shadow\n.end\n"
        with pytest.raises(ValueError, match="resolves outside allowed directories"):
            validate_include_paths(netlist, [tmp_path])


# ===========================================================================
# Class 13: Template Symlink Check
# ===========================================================================


class TestTemplateSymlinkCheck:
    """Verify symlinked user templates are skipped."""

    def test_symlink_template_is_skipped(self, tmp_path):
        user_dir = tmp_path / "templates"
        user_dir.mkdir()

        # Create a real template file
        real_template = {
            "id": "real_test",
            "name": "Real Template",
            "category": "test",
            "description": "A real template",
            "netlist": "* test\n.end\n",
        }
        real_file = user_dir / "real.json"
        real_file.write_text(json.dumps(real_template))

        # Create a symlink template
        target = tmp_path / "external.json"
        target.write_text(
            json.dumps(
                {
                    "id": "symlink_test",
                    "name": "Symlink Template",
                    "category": "test",
                    "description": "A symlinked template",
                    "netlist": "* test\n.end\n",
                }
            )
        )
        symlink_file = user_dir / "symlink.json"
        symlink_file.symlink_to(target)

        tm = TemplateManager()
        nonexistent = tmp_path / "nonexistent"
        with (
            patch.object(TemplateManager, "_user_dir", return_value=user_dir),
            patch.object(TemplateManager, "_builtin_dir", return_value=nonexistent),
        ):
            tm.reload()
            templates = tm.list_templates()
            template_ids = [t["id"] for t in templates]
            assert "real_test" in template_ids
            assert "symlink_test" not in template_ids


# ===========================================================================
# Class 14: Analysis Parameter Casting
# ===========================================================================


class TestAnalysisParameterCasting:
    """Verify analysis tools reject non-numeric parameters."""

    def test_ac_rejects_non_numeric_freq(self):
        setup = create_circuit(_CLEAN_NETLIST)
        assert setup["status"] == "ok"
        cid = setup["circuit_id"]
        result = run_ac_analysis(cid, start_freq="1; .system pwned")
        assert result["status"] == "error"
        assert "Invalid analysis parameter" in result["error"]

    def test_ac_rejects_non_numeric_points(self):
        setup = create_circuit(_CLEAN_NETLIST)
        assert setup["status"] == "ok"
        cid = setup["circuit_id"]
        result = run_ac_analysis(cid, points_per_decade="ten")
        assert result["status"] == "error"
        assert "Invalid analysis parameter" in result["error"]
