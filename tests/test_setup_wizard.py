"""Tests for the setup wizard module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from spicebridge.setup_wizard import (
    _check_cloudflared,
    _check_cloudflared_login,
    _check_ngspice,
    _cloudflared_tunnel_list,
    _detect_existing_config,
    _detect_os,
    _generate_config_yml,
    _install_cloudflared_instructions,
    _named_tunnel_flow,
    _offer_install_cloudflared,
    _parse_simple_yaml,
    _prompt_choice,
    _prompt_string,
    _prompt_yes_no,
    _run_processes,
    _start_tunnel_quick,
    _validate_hostname,
    _validate_tunnel_id,
    _validate_tunnel_name,
    _wait_for_server,
    _write_config_yml,
    run_wizard,
)

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------


class TestCheckCloudflared:
    def test_found(self):
        with patch(
            "spicebridge.setup_wizard.shutil.which", return_value="/usr/bin/cloudflared"
        ):
            assert _check_cloudflared() == "/usr/bin/cloudflared"

    def test_not_found(self):
        with patch("spicebridge.setup_wizard.shutil.which", return_value=None):
            assert _check_cloudflared() is None


class TestCheckNgspice:
    def test_found(self):
        with patch(
            "spicebridge.setup_wizard.shutil.which", return_value="/usr/bin/ngspice"
        ):
            assert _check_ngspice() == "/usr/bin/ngspice"

    def test_not_found(self):
        with patch("spicebridge.setup_wizard.shutil.which", return_value=None):
            assert _check_ngspice() is None


class TestCheckCloudflaredLogin:
    def test_logged_in(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        (config_dir / "cert.pem").touch()
        with patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir):
            assert _check_cloudflared_login() is True

    def test_not_logged_in(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        with patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir):
            assert _check_cloudflared_login() is False


class TestDetectOs:
    def test_macos(self):
        with patch("spicebridge.setup_wizard.platform.system", return_value="Darwin"):
            assert _detect_os() == "macos"

    def test_linux_deb(self):
        with (
            patch("spicebridge.setup_wizard.platform.system", return_value="Linux"),
            patch("spicebridge.setup_wizard.shutil.which", return_value="/usr/bin/apt"),
        ):
            assert _detect_os() == "linux-deb"

    def test_linux_other(self):
        with (
            patch("spicebridge.setup_wizard.platform.system", return_value="Linux"),
            patch("spicebridge.setup_wizard.shutil.which", return_value=None),
        ):
            assert _detect_os() == "linux-other"

    def test_windows(self):
        with patch("spicebridge.setup_wizard.platform.system", return_value="Windows"):
            assert _detect_os() == "other"


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------


class TestGenerateConfigYml:
    def test_basic_config(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        creds = str(config_dir / "abc123.json")
        with patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir):
            result = _generate_config_yml(
                "abc123",
                creds,
                "spice.example.com",
                8000,
            )
        assert "tunnel: abc123" in result
        assert f"credentials-file: {creds}" in result
        assert "hostname: spice.example.com" in result
        assert "service: http://127.0.0.1:8000" in result
        assert "http_status:404" in result

    def test_custom_port(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        creds = str(config_dir / "aabbccdd.json")
        with patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir):
            result = _generate_config_yml(
                "aabbccdd", creds, "z.example.com", 9999, host="10.0.0.1"
            )
        assert "service: http://10.0.0.1:9999" in result

    def test_invalid_tunnel_id_raises(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        creds = str(config_dir / "bad.json")
        with (
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir),
            pytest.raises(ValueError, match="Invalid tunnel ID"),
        ):
            _generate_config_yml("INVALID!", creds, "a.example.com", 8000)

    def test_invalid_hostname_raises(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        creds = str(config_dir / "aabb.json")
        with (
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir),
            pytest.raises(ValueError, match="Invalid hostname"),
        ):
            _generate_config_yml("aabb", creds, "bad host\nname", 8000)


class TestParseSimpleYaml:
    def test_basic_pairs(self):
        text = "tunnel: abc-123\ncredentials-file: /path/to/creds.json\n"
        result = _parse_simple_yaml(text)
        assert result["tunnel"] == "abc-123"
        assert result["credentials-file"] == "/path/to/creds.json"

    def test_ignores_comments(self):
        text = "# this is a comment\ntunnel: abc\n"
        result = _parse_simple_yaml(text)
        assert result == {"tunnel": "abc"}

    def test_ignores_indented_lines(self):
        text = "tunnel: abc\n  - hostname: foo\n    service: bar\n"
        result = _parse_simple_yaml(text)
        assert result == {"tunnel": "abc"}

    def test_ignores_list_items(self):
        text = "tunnel: abc\n- service: http_status:404\n"
        result = _parse_simple_yaml(text)
        assert result == {"tunnel": "abc"}

    def test_empty_string(self):
        assert _parse_simple_yaml("") == {}


class TestDetectExistingConfig:
    def test_no_file(self, tmp_path):
        with patch(
            "spicebridge.setup_wizard._CLOUDFLARED_CONFIG_FILE",
            tmp_path / "nonexistent.yml",
        ):
            assert _detect_existing_config() is None

    def test_valid_config(self, tmp_path):
        config_file = tmp_path / "config.yml"
        config_file.write_text("tunnel: abc-123\ncredentials-file: /path\n")
        with patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_FILE", config_file):
            result = _detect_existing_config()
            assert result is not None
            assert result["tunnel"] == "abc-123"

    def test_malformed_config(self, tmp_path):
        config_file = tmp_path / "config.yml"
        config_file.write_text("just some random text without colons")
        with patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_FILE", config_file):
            # No "tunnel" key => returns None
            assert _detect_existing_config() is None


# ---------------------------------------------------------------------------
# Cloudflared management
# ---------------------------------------------------------------------------


class TestCloudflaredTunnelList:
    def test_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            [
                {"id": "abc-123", "name": "spicebridge"},
            ]
        )
        with patch("spicebridge.setup_wizard.subprocess.run", return_value=mock_result):
            tunnels = _cloudflared_tunnel_list()
            assert len(tunnels) == 1
            assert tunnels[0]["name"] == "spicebridge"

    def test_error(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("spicebridge.setup_wizard.subprocess.run", return_value=mock_result):
            assert _cloudflared_tunnel_list() == []

    def test_timeout(self):
        with patch(
            "spicebridge.setup_wizard.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["cloudflared"], timeout=30),
        ):
            assert _cloudflared_tunnel_list() == []


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


class TestPromptYesNo:
    def test_default_yes(self):
        with patch("builtins.input", return_value=""):
            assert _prompt_yes_no("Test?", default=True) is True

    def test_default_no(self):
        with patch("builtins.input", return_value=""):
            assert _prompt_yes_no("Test?", default=False) is False

    def test_explicit_yes(self):
        with patch("builtins.input", return_value="y"):
            assert _prompt_yes_no("Test?") is True

    def test_explicit_no(self):
        with patch("builtins.input", return_value="n"):
            assert _prompt_yes_no("Test?") is False

    def test_yes_word(self):
        with patch("builtins.input", return_value="yes"):
            assert _prompt_yes_no("Test?") is True

    def test_invalid_then_yes(self):
        with patch("builtins.input", side_effect=["maybe", "y"]):
            assert _prompt_yes_no("Test?") is True


class TestPromptChoice:
    def test_default(self):
        with patch("builtins.input", return_value=""):
            assert _prompt_choice("Pick:", ["A", "B"], default=1) == 1

    def test_explicit_choice(self):
        with patch("builtins.input", return_value="2"):
            assert _prompt_choice("Pick:", ["A", "B"], default=1) == 2

    def test_invalid_then_valid(self):
        with patch("builtins.input", side_effect=["abc", "3", "1"]):
            assert _prompt_choice("Pick:", ["A", "B"], default=1) == 1


# ---------------------------------------------------------------------------
# Install instructions
# ---------------------------------------------------------------------------


class TestInstallInstructions:
    def test_macos(self):
        with patch("spicebridge.setup_wizard._detect_os", return_value="macos"):
            text = _install_cloudflared_instructions()
            assert "brew" in text

    def test_linux_deb(self):
        with patch("spicebridge.setup_wizard._detect_os", return_value="linux-deb"):
            text = _install_cloudflared_instructions()
            assert "apt" in text

    def test_other(self):
        with patch("spicebridge.setup_wizard._detect_os", return_value="other"):
            text = _install_cloudflared_instructions()
            assert "cloudflare.com" in text


# ---------------------------------------------------------------------------
# Integration: run_wizard
# ---------------------------------------------------------------------------


class TestRunWizard:
    def test_quick_tunnel_happy_path(self):
        """Quick tunnel with everything mocked succeeds."""
        mock_server = MagicMock()
        mock_server.poll.return_value = None

        mock_tunnel = MagicMock()
        mock_tunnel.poll.return_value = None

        with (
            patch(
                "spicebridge.setup_wizard._check_ngspice",
                return_value="/usr/bin/ngspice",
            ),
            patch(
                "spicebridge.setup_wizard._check_cloudflared",
                return_value="/usr/bin/cloudflared",
            ),
            patch("spicebridge.setup_wizard._start_server", return_value=mock_server),
            patch("spicebridge.setup_wizard._wait_for_server", return_value=True),
            patch(
                "spicebridge.setup_wizard._start_tunnel_quick",
                return_value=(mock_tunnel, "https://test-abc.trycloudflare.com"),
            ),
            patch("spicebridge.setup_wizard._run_processes", return_value=0),
        ):
            result = run_wizard(["--quick"])
            assert result == 0

    def test_missing_cloudflared_no_install_exits(self):
        """Missing cloudflared with --no-install exits with code 1."""
        with (
            patch(
                "spicebridge.setup_wizard._check_ngspice",
                return_value="/usr/bin/ngspice",
            ),
            patch("spicebridge.setup_wizard._check_cloudflared", return_value=None),
            patch("spicebridge.setup_wizard._detect_os", return_value="other"),
        ):
            result = run_wizard(["--quick", "--no-install"])
            assert result == 1

    def test_server_startup_failure_exits(self):
        """Server failing to start returns exit code 1."""
        mock_server = MagicMock()
        mock_server.poll.return_value = None  # needed for _kill_proc

        with (
            patch(
                "spicebridge.setup_wizard._check_ngspice",
                return_value="/usr/bin/ngspice",
            ),
            patch(
                "spicebridge.setup_wizard._check_cloudflared",
                return_value="/usr/bin/cloudflared",
            ),
            patch("spicebridge.setup_wizard._start_server", return_value=mock_server),
            patch("spicebridge.setup_wizard._wait_for_server", return_value=False),
        ):
            result = run_wizard(["--quick"])
            assert result == 1
            mock_server.terminate.assert_called_once()

    def test_missing_ngspice_decline_exits(self):
        """User declining to continue without ngspice exits with code 1."""
        with (
            patch("spicebridge.setup_wizard._check_ngspice", return_value=None),
            patch("builtins.input", return_value="n"),
        ):
            result = run_wizard(["--quick"])
            assert result == 1

    def test_help_flag(self, capsys):
        """--help flag exits cleanly."""
        with pytest.raises(SystemExit) as exc_info:
            run_wizard(["--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Test 1: _start_tunnel_quick
# ---------------------------------------------------------------------------


class TestStartTunnelQuick:
    def test_url_extraction_normal_line(self):
        """Extract URL from a normal cloudflared stderr line."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr.fileno.return_value = 5
        with (
            patch("spicebridge.setup_wizard.subprocess.Popen", return_value=mock_proc),
            patch("spicebridge.setup_wizard.fcntl.fcntl", return_value=0),
            patch(
                "spicebridge.setup_wizard.os.read",
                return_value=b"INF +---| https://test-abc.trycloudflare.com |---+\n",
            ),
            patch("spicebridge.setup_wizard.time.monotonic", side_effect=[0, 1]),
        ):
            proc, url = _start_tunnel_quick(8000)
            assert "trycloudflare.com" in url
            assert url.startswith("https://")

    def test_bare_hostname(self):
        """Extract URL when only hostname is present (no https://)."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr.fileno.return_value = 5
        with (
            patch("spicebridge.setup_wizard.subprocess.Popen", return_value=mock_proc),
            patch("spicebridge.setup_wizard.fcntl.fcntl", return_value=0),
            patch(
                "spicebridge.setup_wizard.os.read",
                return_value=b"test-abc.trycloudflare.com\n",
            ),
            patch("spicebridge.setup_wizard.time.monotonic", side_effect=[0, 1]),
        ):
            proc, url = _start_tunnel_quick(8000)
            assert url == "https://test-abc.trycloudflare.com"

    def test_no_url_timeout(self):
        """Return empty URL when tunnel never prints a URL."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr.fileno.return_value = 5
        with (
            patch("spicebridge.setup_wizard.subprocess.Popen", return_value=mock_proc),
            patch("spicebridge.setup_wizard.fcntl.fcntl", return_value=0),
            patch(
                "spicebridge.setup_wizard.os.read",
                return_value=b"some other output\n",
            ),
            patch("spicebridge.setup_wizard.time.monotonic", side_effect=[0, 1, 31]),
        ):
            proc, url = _start_tunnel_quick(8000)
            assert url == ""

    def test_trailing_pipe_stripped(self):
        """Trailing pipe character is stripped from URL."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr.fileno.return_value = 5
        with (
            patch("spicebridge.setup_wizard.subprocess.Popen", return_value=mock_proc),
            patch("spicebridge.setup_wizard.fcntl.fcntl", return_value=0),
            patch(
                "spicebridge.setup_wizard.os.read",
                return_value=b"https://test-abc.trycloudflare.com|\n",
            ),
            patch("spicebridge.setup_wizard.time.monotonic", side_effect=[0, 1]),
        ):
            proc, url = _start_tunnel_quick(8000)
            assert not url.endswith("|")
            assert "trycloudflare.com" in url

    def test_partial_line_does_not_hang(self):
        """os.read returns bytes without newline in two chunks, URL still extracted."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr.fileno.return_value = 5
        with (
            patch("spicebridge.setup_wizard.subprocess.Popen", return_value=mock_proc),
            patch("spicebridge.setup_wizard.fcntl.fcntl", return_value=0),
            patch(
                "spicebridge.setup_wizard.os.read",
                side_effect=[
                    b"INF https://test-abc.trycloud",
                    b"flare.com done",
                ],
            ),
            patch("spicebridge.setup_wizard.time.monotonic", side_effect=[0, 1, 2]),
        ):
            proc, url = _start_tunnel_quick(8000)
            assert "trycloudflare.com" in url
            assert url.startswith("https://")


# ---------------------------------------------------------------------------
# Test 2 & 3: _run_processes
# ---------------------------------------------------------------------------


class TestRunProcesses:
    def test_returns_nonzero_on_crash(self):
        """Mock server exits code 1 -> returns 1."""
        server = MagicMock()
        # poll: None (loop check), 1 (loop check -> exits), then non-None for _kill_proc
        server.poll.side_effect = [None, 1, 1, 1]
        server.returncode = 1

        tunnel = MagicMock()
        # poll: None (loop skipped after server exits), then non-None for _kill_proc
        tunnel.poll.return_value = 0
        tunnel.returncode = 0

        result = _run_processes(server, tunnel)
        assert result == 1

    def test_ctrl_c_cleanup(self):
        """KeyboardInterrupt -> both procs get _kill_proc and return 130."""
        server = MagicMock()
        # First poll raises KeyboardInterrupt, then returns None for _kill_proc
        server.poll.side_effect = [KeyboardInterrupt, None]
        server.returncode = None

        tunnel = MagicMock()
        tunnel.poll.return_value = None
        tunnel.returncode = None

        result = _run_processes(server, tunnel)
        assert result == 130
        # Both processes should have terminate called via _kill_proc
        tunnel.terminate.assert_called()
        server.terminate.assert_called()

    def test_returns_130_on_ctrl_c(self):
        """Explicit test: Ctrl+C returns exit code 130."""
        server = MagicMock()
        server.poll.side_effect = [KeyboardInterrupt, None]
        server.returncode = None

        tunnel = MagicMock()
        tunnel.poll.return_value = None
        tunnel.returncode = None

        assert _run_processes(server, tunnel) == 130


# ---------------------------------------------------------------------------
# Test 4: _named_tunnel_flow delete
# ---------------------------------------------------------------------------


class TestNamedTunnelFlowDelete:
    def test_delete_correct_tunnel(self):
        """Multiple tunnels + 'delete' -> correct name passed to delete."""
        import argparse

        args = argparse.Namespace(
            tunnel_name="spicebridge",
            domain="",
            host="127.0.0.1",
            port=8000,
        )
        tunnels = [
            {"id": "aaa", "name": "tunnel-one"},
            {"id": "bbb", "name": "tunnel-two"},
        ]

        with (
            patch(
                "spicebridge.setup_wizard._detect_existing_config", return_value=None
            ),
            patch(
                "spicebridge.setup_wizard._check_cloudflared_login", return_value=True
            ),
            patch(
                "spicebridge.setup_wizard._cloudflared_tunnel_list",
                return_value=tunnels,
            ),
            # choice==3 (delete), then pick tunnel 2 to delete
            patch("spicebridge.setup_wizard._prompt_choice", side_effect=[3, 2]),
            patch(
                "spicebridge.setup_wizard._cloudflared_tunnel_delete", return_value=True
            ) as mock_del,
            patch(
                "spicebridge.setup_wizard._create_new_tunnel", return_value="new-uuid"
            ) as mock_create,
            patch("spicebridge.setup_wizard._prompt_string", return_value=""),
            patch("spicebridge.setup_wizard._start_named_tunnel", return_value=0),
        ):
            _named_tunnel_flow(args)
            mock_del.assert_called_once_with("tunnel-two")
            # C3: create should use the deleted tunnel's name, not the default
            mock_create.assert_called_once()
            assert mock_create.call_args[0][1] == "tunnel-two"


# ---------------------------------------------------------------------------
# Test 5: Hostname validation
# ---------------------------------------------------------------------------


class TestHostnameValidation:
    @pytest.mark.parametrize(
        "hostname",
        [
            "example.com",
            "spicebridge.example.com",
            "a",
            "a1",
            "my-host.example.com",
        ],
    )
    def test_valid(self, hostname):
        assert _validate_hostname(hostname) is True

    @pytest.mark.parametrize(
        "hostname",
        [
            "host\nname",
            "host\n  injected: true",
            "-leading-dash.com",
            "a" * 254,
            "",
            "host name.com",
        ],
    )
    def test_invalid(self, hostname):
        assert _validate_hostname(hostname) is False


# ---------------------------------------------------------------------------
# Test 6: Tunnel name validation
# ---------------------------------------------------------------------------


class TestTunnelNameValidation:
    @pytest.mark.parametrize(
        "name",
        [
            "spicebridge",
            "my-tunnel",
            "tunnel_1",
            "a1b2",
        ],
    )
    def test_valid(self, name):
        assert _validate_tunnel_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "-leading-dash",
            "has spaces",
            "",
            "--flag-like",
        ],
    )
    def test_invalid(self, name):
        assert _validate_tunnel_name(name) is False


# ---------------------------------------------------------------------------
# Test 7: Atomic config write
# ---------------------------------------------------------------------------


class TestWriteConfigYmlAtomic:
    def test_creates_file(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        with (
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir),
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_FILE", config_file),
        ):
            result = _write_config_yml("tunnel: abc\n")
            assert result.read_text() == "tunnel: abc\n"

    def test_creates_backup(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        config_file.write_text("old content")
        with (
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir),
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_FILE", config_file),
        ):
            _write_config_yml("new content")
            backup = Path(str(config_file) + ".bak")
            assert backup.exists()
            assert backup.read_text() == "old content"
            assert config_file.read_text() == "new content"

    def test_preserves_original_on_error(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        config_file.write_text("original")
        with (
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir),
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_FILE", config_file),
            patch("os.replace", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            _write_config_yml("new content")
        assert config_file.read_text() == "original"


# ---------------------------------------------------------------------------
# Test 8: Prompt EOF handling
# ---------------------------------------------------------------------------


class TestPromptEofHandling:
    def test_yes_no_eof_returns_default(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _prompt_yes_no("Test?", default=True) is True

    def test_yes_no_keyboard_interrupt_exits(self):
        with (
            patch("builtins.input", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc_info,
        ):
            _prompt_yes_no("Test?")
        assert exc_info.value.code == 130

    def test_choice_eof_returns_default(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _prompt_choice("Pick:", ["A", "B"], default=2) == 2

    def test_string_eof_returns_default(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _prompt_string("Name", default="fallback") == "fallback"

    def test_string_keyboard_interrupt_exits(self):
        with (
            patch("builtins.input", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc_info,
        ):
            _prompt_string("Name")
        assert exc_info.value.code == 130


# ---------------------------------------------------------------------------
# Test 9: Port validation
# ---------------------------------------------------------------------------


class TestPortValidation:
    @pytest.mark.parametrize("port", [0, -1, 65536])
    def test_invalid_port_rejected(self, port):
        result = run_wizard(["--quick", "--port", str(port)])
        assert result == 1

    def test_valid_port_passes_validation(self):
        """Port 8080 passes the port check (may fail later, but not on port)."""
        with (
            patch(
                "spicebridge.setup_wizard._check_ngspice",
                return_value="/usr/bin/ngspice",
            ),
            patch(
                "spicebridge.setup_wizard._check_cloudflared",
                return_value="/usr/bin/cloudflared",
            ),
            patch("spicebridge.setup_wizard._quick_tunnel_flow", return_value=0),
        ):
            result = run_wizard(["--quick", "--port", "8080"])
            assert result == 0


# ---------------------------------------------------------------------------
# Test 10: Existing config uses correct tunnel name
# ---------------------------------------------------------------------------


class TestExistingConfigUsesTunnel:
    def test_uses_existing_tunnel_not_default(self):
        """Config has 'my-tunnel' -> wizard starts 'my-tunnel', not 'spicebridge'."""
        import argparse

        args = argparse.Namespace(
            tunnel_name="spicebridge",
            domain="",
            host="127.0.0.1",
            port=8000,
        )
        existing = {"tunnel": "my-tunnel", "credentials-file": "/path"}

        with (
            patch(
                "spicebridge.setup_wizard._detect_existing_config",
                return_value=existing,
            ),
            patch("builtins.input", return_value="y"),  # yes, use existing
            patch(
                "spicebridge.setup_wizard._start_named_tunnel", return_value=0
            ) as mock_start,
        ):
            result = _named_tunnel_flow(args)
            assert result == 0
            mock_start.assert_called_once_with(args, "my-tunnel")


# ---------------------------------------------------------------------------
# Test C5: Tunnel ID validation
# ---------------------------------------------------------------------------


class TestValidateTunnelId:
    @pytest.mark.parametrize(
        "tunnel_id",
        [
            "aabbccdd",
            "abc123",
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "aa",
            "0123456789abcdef",
        ],
    )
    def test_valid(self, tunnel_id):
        assert _validate_tunnel_id(tunnel_id) is True

    @pytest.mark.parametrize(
        "tunnel_id",
        [
            "",
            "ABCDEF",
            "abc\n123",
            "../etc/passwd",
            "a" * 65,
            "a",  # single char (regex requires at least 2)
            "-abc123",
        ],
    )
    def test_invalid(self, tunnel_id):
        assert _validate_tunnel_id(tunnel_id) is False


# ---------------------------------------------------------------------------
# Test H1: Non-TTY stdin skips install
# ---------------------------------------------------------------------------


class TestOfferInstallCloudflared:
    def test_non_tty_stdin_skips_install(self):
        """Non-interactive mode returns False without prompting."""
        with (
            patch("spicebridge.setup_wizard._detect_os", return_value="linux-deb"),
            patch("spicebridge.setup_wizard.sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = False
            result = _offer_install_cloudflared()
            assert result is False


# ---------------------------------------------------------------------------
# Test H6: Dead server returns False
# ---------------------------------------------------------------------------


class TestWaitForServer:
    def test_dead_server_returns_false(self):
        """server_proc.poll() returning non-None -> returns False immediately."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process already exited
        result = _wait_for_server("127.0.0.1", 8000, server_proc=mock_proc)
        assert result is False


# ---------------------------------------------------------------------------
# Test H7/M1: File permissions 0o600
# ---------------------------------------------------------------------------


class TestWriteConfigYmlPermissions:
    def test_file_permissions_0600(self, tmp_path):
        config_dir = tmp_path / ".cloudflared"
        config_dir.mkdir()
        config_file = config_dir / "config.yml"
        with (
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_DIR", config_dir),
            patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_FILE", config_file),
        ):
            result = _write_config_yml("tunnel: abc\n")
            assert result.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# Test M4: Non-UTF-8 config file
# ---------------------------------------------------------------------------


class TestDetectExistingConfigNonUtf8:
    def test_non_utf8_file(self, tmp_path):
        config_file = tmp_path / "config.yml"
        config_file.write_bytes(b"\x80\x81\x82\xff\xfe")
        with patch("spicebridge.setup_wizard._CLOUDFLARED_CONFIG_FILE", config_file):
            assert _detect_existing_config() is None


# ---------------------------------------------------------------------------
# Test M5: Colon in YAML values
# ---------------------------------------------------------------------------


class TestParseSimpleYamlColonInValue:
    def test_colon_in_value_preserved(self):
        text = "service: http://127.0.0.1:8000\n"
        result = _parse_simple_yaml(text)
        assert result["service"] == "http://127.0.0.1:8000"

    def test_http_status_value(self):
        text = "service: http_status:404\n"
        result = _parse_simple_yaml(text)
        assert result["service"] == "http_status:404"
