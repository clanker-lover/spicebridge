"""Tests for spicebridge.sanitize — input validation and security."""

import pytest

from spicebridge.sanitize import (
    MAX_NETLIST_SIZE,
    safe_error_response,
    safe_path,
    sanitize_error,
    sanitize_netlist,
    validate_component_value,
    validate_filename,
    validate_format,
)

# ---------------------------------------------------------------------------
# sanitize_netlist
# ---------------------------------------------------------------------------


class TestSanitizeNetlist:
    """Tests for the netlist sanitizer."""

    def test_clean_netlist_passes(self):
        netlist = """\
RC Low-Pass Filter
V1 in 0 AC 1
R1 in out 1k
C1 out 0 1u
.ac dec 10 1 1meg
.end
"""
        assert sanitize_netlist(netlist) == netlist

    def test_comments_are_allowed(self):
        netlist = "* This is a comment\nR1 1 2 1k\n.end\n"
        assert sanitize_netlist(netlist) == netlist

    def test_empty_lines_are_allowed(self):
        netlist = "\n\nR1 1 2 1k\n\n.end\n"
        assert sanitize_netlist(netlist) == netlist

    @pytest.mark.parametrize(
        "directive",
        [
            ".system echo pwned",
            ".SYSTEM rm -rf /",
            ".exec /bin/sh",
            ".shell ls",
            ".control",
            ".endc",
            ".python",
            ".csparam test",
            "  .system echo test",
            "\t.EXEC cmd",
        ],
    )
    def test_dangerous_directives_rejected(self, directive):
        netlist = f"Test\n{directive}\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)

    def test_include_rejected_by_default(self):
        netlist = "Test\n.include /etc/passwd\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)

    def test_lib_rejected_by_default(self):
        netlist = "Test\n.lib /some/path\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)

    def test_include_allowed_with_flag(self):
        netlist = "Test\n.include /models/test.lib\nR1 1 2 1k\n.end\n"
        assert sanitize_netlist(netlist, _allow_includes=True) == netlist

    def test_backtick_rejected(self):
        netlist = "Test\nR1 1 2 `echo 1k`\n.end\n"
        with pytest.raises(ValueError, match="[Bb]acktick"):
            sanitize_netlist(netlist)

    def test_size_limit_enforced(self):
        netlist = "x" * (MAX_NETLIST_SIZE + 1)
        with pytest.raises(ValueError, match="too large"):
            sanitize_netlist(netlist)

    def test_size_at_limit_passes(self):
        netlist = "R1 1 2 1k\n" * (MAX_NETLIST_SIZE // 10)
        netlist = netlist[:MAX_NETLIST_SIZE]
        sanitize_netlist(netlist)  # should not raise

    def test_safe_directives_pass(self):
        """Standard SPICE directives should not be blocked."""
        netlist = (
            "Test\n"
            ".ac dec 10 1 1meg\n"
            ".tran 1u 10m\n"
            ".op\n"
            ".param R1=1k\n"
            ".model NPN NPN\n"
            ".subckt test 1 2\n"
            ".ends\n"
            ".end\n"
        )
        assert sanitize_netlist(netlist) == netlist

    def test_control_block_rejected(self):
        netlist = "Test\n.control\nshell echo PWNED\n.endc\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)

    def test_script_block_rejected(self):
        netlist = "Test\n.script\nprint('hi')\n.endscript\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)

    def test_rawfile_rejected(self):
        netlist = "Test\n.rawfile /tmp/out.raw\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)

    def test_shell_rejected(self):
        netlist = "Test\n.shell echo pwned\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)

    def test_all_allowlisted_directives_pass(self):
        """Every directive in the allowlist should pass validation."""
        lines = [
            "Test Circuit",
            ".ac dec 10 1 1meg",
            ".tran 1u 10m",
            ".op",
            ".dc V1 0 5 0.1",
            ".param R1=1k",
            ".subckt myblock 1 2",
            ".ends myblock",
            ".model NPN NPN",
            ".global gnd",
            ".ic V(out)=0",
            ".nodeset V(out)=2.5",
            ".options reltol=0.001",
            ".temp 27",
            ".save all",
            ".end",
        ]
        netlist = "\n".join(lines) + "\n"
        # .include and .lib require _allow_includes=True
        assert sanitize_netlist(netlist) == netlist
        inc_netlist = "Test\n.include /models/test.lib\n.lib /models/lib2\n.end\n"
        assert sanitize_netlist(inc_netlist, _allow_includes=True) == inc_netlist

    def test_continuation_line_reassembly_catches_split(self):
        """A directive split across continuation lines must be caught."""
        netlist = "Test\n.con\n+trol\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)

    def test_unknown_directive_rejected(self):
        netlist = "Test\n.foobar something\n.end\n"
        with pytest.raises(ValueError, match="not allowed"):
            sanitize_netlist(netlist)


# ---------------------------------------------------------------------------
# validate_component_value
# ---------------------------------------------------------------------------


class TestValidateComponentValue:
    """Tests for component value validation."""

    @pytest.mark.parametrize(
        "value",
        [
            "1k",
            "10meg",
            "100u",
            "4.7n",
            "1.5",
            "0",
            "{R1*2}",
            "100p",
            "3.3k",
            "open",
            "2N2222",
        ],
    )
    def test_valid_values_pass(self, value):
        assert validate_component_value(value) == value

    def test_empty_value_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            validate_component_value("")

    def test_newline_rejected(self):
        with pytest.raises(ValueError, match="newline"):
            validate_component_value("1k\n.system echo pwned")

    def test_carriage_return_rejected(self):
        with pytest.raises(ValueError, match="newline"):
            validate_component_value("1k\r.system echo pwned")

    def test_semicolon_rejected(self):
        with pytest.raises(ValueError, match="semicolon"):
            validate_component_value("1k; echo pwned")

    def test_backtick_rejected(self):
        with pytest.raises(ValueError, match="backtick"):
            validate_component_value("`echo 1k`")

    def test_dot_prefix_rejected(self):
        with pytest.raises(ValueError, match="directive"):
            validate_component_value(".system echo pwned")

    def test_special_chars_rejected(self):
        with pytest.raises(ValueError, match="disallowed"):
            validate_component_value("1k$PWD")


# ---------------------------------------------------------------------------
# safe_path
# ---------------------------------------------------------------------------


class TestSafePath:
    """Tests for path traversal prevention."""

    def test_normal_path(self, tmp_path):
        result = safe_path(tmp_path, "output.txt")
        assert result == tmp_path / "output.txt"

    def test_subdirectory(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        result = safe_path(tmp_path, "sub/output.txt")
        assert result == sub / "output.txt"

    def test_traversal_blocked(self, tmp_path):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            safe_path(tmp_path, "../../etc/passwd")

    def test_absolute_path_blocked(self, tmp_path):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            safe_path(tmp_path, "/etc/passwd")

    def test_dot_dot_in_middle_blocked(self, tmp_path):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            safe_path(tmp_path, "sub/../../etc/passwd")


# ---------------------------------------------------------------------------
# validate_filename
# ---------------------------------------------------------------------------


class TestValidateFilename:
    """Tests for filename validation."""

    def test_valid_filename(self):
        assert validate_filename("circuit.kicad_sch") == "circuit.kicad_sch"

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            validate_filename("")

    def test_forward_slash_rejected(self):
        with pytest.raises(ValueError, match="path separator"):
            validate_filename("../../etc/test")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="path separator"):
            validate_filename("..\\..\\etc\\test")

    def test_dot_dot_rejected(self):
        with pytest.raises(ValueError, match="\\.\\."):
            validate_filename("..passwd")


# ---------------------------------------------------------------------------
# validate_format
# ---------------------------------------------------------------------------


class TestValidateFormat:
    """Tests for schematic format validation."""

    @pytest.mark.parametrize("fmt", ["png", "svg", "pdf"])
    def test_valid_formats(self, fmt):
        assert validate_format(fmt) == fmt

    def test_invalid_format_rejected(self):
        with pytest.raises(ValueError, match="Invalid format"):
            validate_format("exe")

    def test_traversal_in_format_rejected(self):
        with pytest.raises(ValueError, match="Invalid format"):
            validate_format("../../../etc/passwd")

    def test_empty_format_rejected(self):
        with pytest.raises(ValueError, match="Invalid format"):
            validate_format("")


# ---------------------------------------------------------------------------
# sanitize_error / safe_error_response
# ---------------------------------------------------------------------------


class TestSanitizeError:
    """Tests for error message path scrubbing."""

    def test_strips_home_path(self):
        msg = sanitize_error(RuntimeError("Cannot open /home/user/project/data.txt"))
        assert "/home/" not in msg
        assert "<path>" in msg

    def test_strips_tmp_path(self):
        msg = sanitize_error(OSError("No such file: /tmp/spicebridge_abc123/out.raw"))
        assert "/tmp/" not in msg
        assert "<path>" in msg

    def test_preserves_non_path_message(self):
        msg = sanitize_error(ValueError("points_per_decade must be between 1 and 1000"))
        assert msg == "points_per_decade must be between 1 and 1000"

    def test_multiple_paths_stripped(self):
        msg = sanitize_error(RuntimeError("copy /home/a/x to /tmp/b/y failed"))
        assert "/home/" not in msg
        assert "/tmp/" not in msg
        assert msg.count("<path>") == 2


class TestSafeErrorResponse:
    def test_returns_error_dict(self):
        import logging

        lgr = logging.getLogger("test.safe_error_response")
        exc = RuntimeError("Failed at /home/user/project/sim.raw")
        result = safe_error_response(exc, lgr, "test_context")
        assert result["status"] == "error"
        assert "/home/" not in result["error"]
        assert "<path>" in result["error"]
