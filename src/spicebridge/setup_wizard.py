"""Interactive setup wizard for SPICEBridge + Cloudflare tunnel.

Walks the user through installing cloudflared, creating a tunnel,
generating config, and running both SPICEBridge and the tunnel together.

Usage:
    spicebridge setup-cloud              # interactive wizard
    spicebridge setup-cloud --quick      # quick tunnel (no account needed)
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PORT = 8000
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_TRANSPORT = "streamable-http"

_CLOUDFLARED_CONFIG_DIR = Path.home() / ".cloudflared"
_CLOUDFLARED_CONFIG_FILE = _CLOUDFLARED_CONFIG_DIR / "config.yml"

_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?$")
_TUNNEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_TUNNEL_ID_RE = re.compile(r"^[a-f0-9][a-f0-9-]*[a-f0-9]$")


def _validate_hostname(hostname: str) -> bool:
    """Return True if hostname is safe for use in config."""
    return len(hostname) <= 253 and _HOSTNAME_RE.match(hostname) is not None


def _validate_tunnel_name(name: str) -> bool:
    """Return True if tunnel name is safe for CLI use."""
    return bool(name) and _TUNNEL_NAME_RE.match(name) is not None


def _validate_tunnel_id(tunnel_id: str) -> bool:
    """Return True if tunnel_id looks like a valid hex/UUID string."""
    return (
        bool(tunnel_id)
        and len(tunnel_id) <= 64
        and _TUNNEL_ID_RE.match(tunnel_id) is not None
    )


_BANNER = """\
╔══════════════════════════════════════════╗
║   SPICEBridge Cloud Setup Wizard         ║
╚══════════════════════════════════════════╝
"""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_wizard_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="spicebridge setup-cloud",
        description="Guided setup for SPICEBridge + Cloudflare tunnel.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use a temporary quick tunnel (no Cloudflare account needed)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"Local port for SPICEBridge server (default: {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        default=_DEFAULT_HOST,
        help=f"Local host for SPICEBridge server (default: {_DEFAULT_HOST})",
    )
    parser.add_argument(
        "--tunnel-name",
        default="spicebridge",
        help="Name for the Cloudflare tunnel (default: spicebridge)",
    )
    parser.add_argument(
        "--domain",
        default="",
        help="Custom domain/hostname for the tunnel (e.g. spicebridge.example.com)",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Skip automatic cloudflared installation",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------


def _check_cloudflared() -> str | None:
    """Return path to cloudflared if found, else None."""
    return shutil.which("cloudflared")


def _check_ngspice() -> str | None:
    """Return path to ngspice if found, else None."""
    return shutil.which("ngspice")


def _check_cloudflared_login() -> bool:
    """Return True if cloudflared has a cert.pem (user is logged in)."""
    return (_CLOUDFLARED_CONFIG_DIR / "cert.pem").exists()


def _detect_os() -> str:
    """Detect OS for install instructions."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        if shutil.which("apt") is not None:
            return "linux-deb"
        return "linux-other"
    return "other"


# ---------------------------------------------------------------------------
# Install helper
# ---------------------------------------------------------------------------


def _install_cloudflared_instructions() -> str:
    """Return OS-specific install instructions."""
    os_type = _detect_os()
    if os_type == "macos":
        return (
            "Install cloudflared:\n"
            "  brew install cloudflared\n"
            "\n"
            "Or download from:\n"
            "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        )
    if os_type == "linux-deb":
        return (
            "Install cloudflared:\n"
            "  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg "
            "| sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null\n"
            "  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
            "https://pkg.cloudflare.com/cloudflared '$(lsb_release -cs)' main' "
            "| sudo tee /etc/apt/sources.list.d/cloudflared.list\n"
            "  sudo apt update && sudo apt install cloudflared\n"
        )
    return (
        "Download cloudflared from:\n"
        "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    )


def _offer_install_cloudflared(no_install: bool = False) -> bool:
    """Prompt to install cloudflared. Returns True if installed successfully."""
    if no_install:
        print(_install_cloudflared_instructions())
        return False

    os_type = _detect_os()
    if os_type not in ("macos", "linux-deb"):
        print(_install_cloudflared_instructions())
        return False

    if not sys.stdin.isatty():
        print("Non-interactive mode detected. Skipping automatic install.")
        print("Install cloudflared manually, then re-run the wizard.")
        return False

    if not _prompt_yes_no("cloudflared not found. Attempt automatic install?"):
        print(_install_cloudflared_instructions())
        return False

    try:
        if os_type == "macos":
            print("Running: brew install cloudflared")
            subprocess.run(
                ["brew", "install", "cloudflared"],
                check=True,
                timeout=120,
            )
        else:
            print("Installing cloudflared via apt...")
            subprocess.run(
                ["sudo", "apt", "update"],
                check=True,
                timeout=60,
            )
            subprocess.run(
                ["sudo", "apt", "install", "-y", "cloudflared"],
                check=True,
                timeout=60,
            )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        print("Automatic install failed.")
        print(_install_cloudflared_instructions())
        return False

    if _check_cloudflared():
        print("cloudflared installed successfully.")
        return True

    print("cloudflared still not found on PATH after install.")
    return False


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


def _prompt_yes_no(question: str, default: bool = True) -> bool:
    """Ask a yes/no question. Returns bool."""
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            answer = input(question + suffix).strip().lower()
        except EOFError:
            return default
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        if answer == "":
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


def _prompt_choice(question: str, options: list[str], default: int = 1) -> int:
    """Show numbered menu, return 1-based index."""
    print(question)
    for i, opt in enumerate(options, 1):
        marker = " *" if i == default else ""
        print(f"  {i}) {opt}{marker}")
    while True:
        try:
            answer = input(f"Choice [{default}]: ").strip()
        except EOFError:
            return default
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        if answer == "":
            return default
        try:
            choice = int(answer)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        print(f"Please enter a number 1-{len(options)}.")


def _prompt_string(question: str, default: str = "") -> str:
    """Ask for a string value with optional default."""
    try:
        if default:
            answer = input(f"{question} [{default}]: ").strip()
            return answer if answer else default
        return input(f"{question}: ").strip()
    except EOFError:
        return default
    except KeyboardInterrupt:
        raise SystemExit(130) from None


# ---------------------------------------------------------------------------
# Cloudflared management
# ---------------------------------------------------------------------------


def _cloudflared_tunnel_list() -> list[dict]:
    """Return list of existing tunnels as dicts with 'id' and 'name' keys."""
    try:
        result = subprocess.run(
            ["cloudflared", "tunnel", "list", "--output", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def _cloudflared_tunnel_create(name: str) -> str:
    """Create a tunnel and return its UUID."""
    try:
        result = subprocess.run(
            ["cloudflared", "tunnel", "create", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Timed out creating tunnel '{name}'") from e
    except FileNotFoundError as e:
        raise RuntimeError("cloudflared not found on PATH") from e
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create tunnel '{name}': {result.stderr.strip()}")
    # Parse UUID from output like "Created tunnel <name> with id <uuid>"
    for line in (result.stdout + result.stderr).splitlines():
        if "with id" in line.lower():
            parts = line.strip().split()
            return parts[-1]
    raise RuntimeError(f"Could not parse tunnel ID from: {result.stdout}")


def _cloudflared_tunnel_delete(name: str) -> bool:
    """Delete a tunnel by name. Returns True on success."""
    try:
        result = subprocess.run(
            ["cloudflared", "tunnel", "delete", "--force", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


def _cloudflared_tunnel_route_dns(tunnel_name: str, hostname: str) -> bool:
    """Route DNS for a hostname to a tunnel. Returns True on success."""
    if not _validate_hostname(hostname):
        print(f"Error: Invalid hostname: {hostname!r}")
        return False
    result = subprocess.run(
        ["cloudflared", "tunnel", "route", "dns", tunnel_name, hostname],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"Warning: DNS routing failed: {result.stderr.strip()}")
        return False
    return True


# ---------------------------------------------------------------------------
# Config generation (string formatting, no PyYAML)
# ---------------------------------------------------------------------------


def _generate_config_yml(
    tunnel_id: str,
    credentials_file: str,
    hostname: str,
    local_port: int,
    host: str = "127.0.0.1",
) -> str:
    """Generate cloudflared config.yml content."""
    if not _validate_hostname(hostname):
        raise ValueError(f"Invalid hostname: {hostname!r}")
    if not _validate_tunnel_id(tunnel_id):
        raise ValueError(f"Invalid tunnel ID: {tunnel_id!r}")
    creds_path = Path(credentials_file).resolve()
    config_dir = _CLOUDFLARED_CONFIG_DIR.resolve()
    if not str(creds_path).startswith(str(config_dir) + os.sep):
        raise ValueError(f"Credentials file outside config dir: {credentials_file}")
    return (
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {credentials_file}\n"
        f"\n"
        f"ingress:\n"
        f"  - hostname: {hostname}\n"
        f"    service: http://{host}:{local_port}\n"
        f"  - service: http_status:404\n"
    )


def _write_config_yml(content: str) -> Path:
    """Write config.yml atomically to ~/.cloudflared/. Returns the path."""
    _CLOUDFLARED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_file = _CLOUDFLARED_CONFIG_FILE

    # Backup existing config
    if config_file.exists():
        shutil.copy2(config_file, str(config_file) + ".bak")

    # Atomic write: write to temp file then rename
    fd, tmp = tempfile.mkstemp(dir=str(_CLOUDFLARED_CONFIG_DIR))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(config_file))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

    print(f"Wrote {config_file}")
    return config_file


def _parse_simple_yaml(text: str) -> dict:
    """Parse top-level key: value pairs from simple YAML. Ignores indented lines."""
    result = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        if line[0] in (" ", "\t"):
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            result[key.strip()] = value.strip()
    return result


def _detect_existing_config() -> dict | None:
    """Parse existing config.yml if present. Returns dict or None."""
    if not _CLOUDFLARED_CONFIG_FILE.exists():
        return None
    try:
        text = _CLOUDFLARED_CONFIG_FILE.read_text()
        parsed = _parse_simple_yaml(text)
        if "tunnel" in parsed:
            return parsed
    except (OSError, UnicodeDecodeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------


def _kill_proc(proc: subprocess.Popen) -> None:
    """Terminate a process cleanly, avoiding zombies and port leaks."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _generate_api_key() -> str:
    """Generate a random API key."""
    return secrets.token_urlsafe(32)


def _start_server(
    host: str, port: int, transport: str, api_key: str = ""
) -> subprocess.Popen:
    """Start SPICEBridge MCP server as a subprocess."""
    env = None
    if api_key:
        env = {**os.environ, "SPICEBRIDGE_API_KEY": api_key}
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "spicebridge",
            "--transport",
            transport,
            "--host",
            host,
            "--port",
            str(port),
        ],
        env=env,
    )


def _wait_for_server(
    host: str, port: int, timeout: int = 30, server_proc: subprocess.Popen | None = None
) -> bool:
    """Wait for the server to respond. Returns True if ready."""
    url = f"http://{host}:{port}/mcp"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_proc is not None and server_proc.poll() is not None:
            return False
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2):  # noqa: S310
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def _start_tunnel_quick(local_port: int) -> tuple[subprocess.Popen, str]:
    """Start a quick tunnel. Returns (process, tunnel_url)."""
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{local_port}"],
        stderr=subprocess.PIPE,
    )
    # Parse the tunnel URL from stderr output.
    # Uses fcntl + os.read to avoid blocking on partial lines (Unix-only).
    fd = proc.stderr.fileno()  # type: ignore[union-attr]
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    url = ""
    buf = b""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            chunk = os.read(fd, 4096)
            if not chunk:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            buf += chunk
        except BlockingIOError:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
            continue

        text = buf.decode("utf-8", errors="replace")
        if "trycloudflare.com" in text:
            for word in text.split():
                if "trycloudflare.com" in word:
                    url = word.strip().rstrip("|")
                    if not url.startswith("http"):
                        url = "https://" + url
                    break
            if url:
                break

    # Drain remaining stderr in background to prevent 64KB pipe buffer deadlock.
    def _drain_stderr() -> None:
        try:
            while True:
                data = os.read(fd, 4096)
                if not data:
                    break
        except OSError:
            pass

    if url:
        t = threading.Thread(target=_drain_stderr, daemon=True)
        t.start()

    return proc, url


def _start_tunnel_named(tunnel_name: str) -> subprocess.Popen:
    """Start a named tunnel."""
    return subprocess.Popen(
        ["cloudflared", "tunnel", "run", tunnel_name],
    )


def _run_processes(server_proc: subprocess.Popen, tunnel_proc: subprocess.Popen) -> int:
    """Block until Ctrl+C, then cleanly shut down both processes."""
    interrupted = False

    def _shutdown(signum, frame):  # noqa: ARG001, ANN001
        raise KeyboardInterrupt

    original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        while True:
            if server_proc.poll() is not None:
                print(f"\nServer process exited (code {server_proc.returncode}).")
                break
            if tunnel_proc.poll() is not None:
                print(f"\nTunnel process exited (code {tunnel_proc.returncode}).")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        interrupted = True
    finally:
        signal.signal(signal.SIGINT, original_sigint)
        for proc in (tunnel_proc, server_proc):
            _kill_proc(proc)

    if interrupted:
        return 130
    if (server_proc.returncode or 0) != 0 or (tunnel_proc.returncode or 0) != 0:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_connection_info(url: str, is_permanent: bool, api_key: str = "") -> None:
    """Print the connection info box."""
    tunnel_type = "Permanent" if is_permanent else "Temporary quick"
    print()
    print("=" * 50)
    print("  SPICEBridge cloud MCP server is running")
    print("=" * 50)
    print()
    print(f"  {tunnel_type} URL: {url}/mcp")
    print()
    if api_key:
        print(f"  API Key: {api_key}")
        print()
        print("  JSON config snippet:")
        print("  {")
        print(f'    "url": "{url}/mcp",')
        print(f'    "headers": {{"X-API-Key": "{api_key}"}}')
        print("  }")
        print()
    print("  To connect from Claude.ai:")
    print("  1. Go to Settings > MCP Servers")
    print(f"  2. Add server URL: {url}/mcp")
    print()
    if not is_permanent:
        print("  Note: This URL is temporary and will change on restart.")
        print("  For a permanent URL, run: spicebridge setup-cloud")
        if api_key:
            print(
                "  Warning: This API key is temporary and dies when the wizard stops."
            )
        print()
    print("  Press Ctrl+C to stop.")
    print()


# ---------------------------------------------------------------------------
# Main wizard flow
# ---------------------------------------------------------------------------


def run_wizard(argv: list[str] | None = None) -> int:
    """Run the interactive setup wizard. Returns exit code."""
    args = _parse_wizard_args(argv)

    # --- Validate args ---
    if not (1 <= args.port <= 65535):
        print(f"Error: Port must be between 1 and 65535, got {args.port}.")
        return 1

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"Warning: --host {args.host} will bind the server"
            " to a non-loopback address."
        )
        print("The Cloudflare tunnel always targets 127.0.0.1.")
        print("Use --host 127.0.0.1 (default) unless you know what you're doing.")
        if not _prompt_yes_no("Continue anyway?", default=False):
            return 1

    if args.domain and not _validate_hostname(args.domain):
        print(f"Error: Invalid hostname: {args.domain!r}")
        return 1

    if args.tunnel_name and not _validate_tunnel_name(args.tunnel_name):
        print(f"Error: Invalid tunnel name: {args.tunnel_name!r}")
        return 1

    print(_BANNER)

    # --- Check ngspice ---
    if not _check_ngspice():
        print("Warning: ngspice not found on PATH.")
        print("SPICEBridge requires ngspice for circuit simulation.")
        if not _prompt_yes_no("Continue without ngspice?", default=False):
            return 1

    # --- Check cloudflared ---
    if not _check_cloudflared() and not _offer_install_cloudflared(
        no_install=args.no_install
    ):
        return 1

    # --- Quick tunnel flow ---
    if args.quick:
        return _quick_tunnel_flow(args)

    # --- Ask user: quick or permanent? ---
    choice = _prompt_choice(
        "Which tunnel type?",
        [
            "Quick tunnel (temporary URL, no account needed)",
            "Named tunnel (permanent URL, requires Cloudflare account)",
        ],
        default=1,
    )
    if choice == 1:
        return _quick_tunnel_flow(args)

    # --- Named tunnel flow ---
    return _named_tunnel_flow(args)


def _quick_tunnel_flow(args: argparse.Namespace) -> int:
    """Run the quick tunnel flow."""
    print("\nStarting quick tunnel...")

    api_key = _generate_api_key()
    server_proc = _start_server(
        args.host, args.port, _DEFAULT_TRANSPORT, api_key=api_key
    )
    try:
        print(f"Server starting on {args.host}:{args.port}...")

        if not _wait_for_server(args.host, args.port, server_proc=server_proc):
            print("Error: Server failed to start within 30 seconds.")
            _kill_proc(server_proc)
            return 1

        print("Server ready.")
        tunnel_proc, url = _start_tunnel_quick(args.port)
        try:
            if not url:
                print("Error: Could not obtain quick tunnel URL.")
                _kill_proc(tunnel_proc)
                _kill_proc(server_proc)
                return 1

            _print_connection_info(url, is_permanent=False, api_key=api_key)
            return _run_processes(server_proc, tunnel_proc)
        except BaseException:
            _kill_proc(tunnel_proc)
            raise
    except BaseException:
        _kill_proc(server_proc)
        raise


def _named_tunnel_flow(args: argparse.Namespace) -> int:
    """Run the named tunnel flow."""
    # Check for existing config
    existing = _detect_existing_config()
    if existing and "tunnel" in existing:
        print(f"\nExisting tunnel config found: tunnel={existing['tunnel']}")
        if _prompt_yes_no("Use existing config and start?"):
            return _start_named_tunnel(args, existing["tunnel"])

    # Check login
    if not _check_cloudflared_login():
        print("\nYou need to log in to Cloudflare.")
        print("Running: cloudflared tunnel login")
        result = subprocess.run(
            ["cloudflared", "tunnel", "login"],
            timeout=600,
        )
        if result.returncode != 0:
            print("Login failed.")
            return 1

    # List existing tunnels
    tunnels = [t for t in _cloudflared_tunnel_list() if t.get("name")]
    tunnel_name = args.tunnel_name

    if tunnels:
        names = [t.get("name", "unnamed") for t in tunnels]
        print(f"\nExisting tunnels: {', '.join(names)}")

        choice = _prompt_choice(
            "What would you like to do?",
            ["Reuse existing tunnel", "Create new tunnel", "Delete and recreate"],
            default=1,
        )
        if choice == 1:
            # Pick which tunnel
            if len(tunnels) == 1:
                tunnel_name = tunnels[0].get("name", tunnel_name)
            else:
                idx = _prompt_choice(
                    "Which tunnel?",
                    [t.get("name", "unnamed") for t in tunnels],
                    default=1,
                )
                tunnel_name = tunnels[idx - 1].get("name", tunnel_name)
        elif choice == 3:
            # Fix 5: delete the correct tunnel
            if len(tunnels) == 1:
                del_name = tunnels[0].get("name", tunnel_name)
            else:
                idx = _prompt_choice(
                    "Which tunnel to delete?",
                    [t.get("name", "unnamed") for t in tunnels],
                    default=1,
                )
                del_name = tunnels[idx - 1].get("name", tunnel_name)
            if not _cloudflared_tunnel_delete(del_name):
                print(f"Error: Failed to delete tunnel '{del_name}'.")
                return 1
            tunnel_id = _create_new_tunnel(args, del_name)
            if not tunnel_id:
                print("Error: Failed to create new tunnel.")
                return 1
            tunnel_name = del_name
        else:
            tunnel_name = _prompt_string("Tunnel name", default=tunnel_name)
            while not _validate_tunnel_name(tunnel_name):
                print("Invalid tunnel name. Use alphanumeric, hyphens, underscores.")
                tunnel_name = _prompt_string("Tunnel name", default=args.tunnel_name)
            tunnel_id = _create_new_tunnel(args, tunnel_name)
            if not tunnel_id:
                print("Error: Failed to create new tunnel.")
                return 1
    else:
        tunnel_name = _prompt_string("Tunnel name", default=tunnel_name)
        while not _validate_tunnel_name(tunnel_name):
            print("Invalid tunnel name. Use alphanumeric, hyphens, underscores.")
            tunnel_name = _prompt_string("Tunnel name", default=args.tunnel_name)
        tunnel_id = _create_new_tunnel(args, tunnel_name)
        if not tunnel_id:
            print("Error: Failed to create new tunnel.")
            return 1

    # Ask for domain if not provided, with validation
    hostname = args.domain
    if not hostname:
        hostname = _prompt_string(
            "Hostname for the tunnel (e.g. spicebridge.example.com)",
            default="",
        )
        while hostname and not _validate_hostname(hostname):
            print("Invalid hostname.")
            hostname = _prompt_string(
                "Hostname for the tunnel (e.g. spicebridge.example.com)",
                default="",
            )

    # Generate config if we have enough info
    if not hostname:
        print("No hostname provided. Skipping config generation and DNS routing.")
        print(
            "You'll need to configure the tunnel manually via the Cloudflare dashboard."
        )

    if hostname:
        tunnels = [t for t in _cloudflared_tunnel_list() if t.get("name")]
        tunnel_id = ""
        for t in tunnels:
            if t.get("name") == tunnel_name:
                tunnel_id = t.get("id", "")
                break

        if tunnel_id:
            creds_file = str(_CLOUDFLARED_CONFIG_DIR / f"{tunnel_id}.json")
            config = _generate_config_yml(
                tunnel_id, creds_file, hostname, args.port, host=args.host
            )

            write_config = True
            if _CLOUDFLARED_CONFIG_FILE.exists() and not _prompt_yes_no(
                "Overwrite existing config.yml?"
            ):
                print("Keeping existing config.")
                write_config = False
            if write_config:
                _write_config_yml(config)
                print(f"\nRouting DNS: {hostname} -> tunnel {tunnel_name}")
                _cloudflared_tunnel_route_dns(tunnel_name, hostname)

    return _start_named_tunnel(args, tunnel_name)


def _create_new_tunnel(args: argparse.Namespace, tunnel_name: str) -> str:  # noqa: ARG001
    """Create a new tunnel and return its UUID."""
    print(f"\nCreating tunnel '{tunnel_name}'...")
    try:
        tunnel_id = _cloudflared_tunnel_create(tunnel_name)
        if not _validate_tunnel_id(tunnel_id):
            print(f"Warning: Unexpected tunnel ID format: {tunnel_id!r}")
        print(f"Tunnel created: {tunnel_id}")
        return tunnel_id
    except RuntimeError as e:
        print(f"Error: {e}")
        return ""


def _start_named_tunnel(args: argparse.Namespace, tunnel_name: str) -> int:
    """Start server + named tunnel."""
    api_key = _prompt_string("API key (leave blank to auto-generate)", default="")
    if not api_key:
        api_key = _generate_api_key()

    server_proc = _start_server(
        args.host, args.port, _DEFAULT_TRANSPORT, api_key=api_key
    )
    try:
        print(f"\nServer starting on {args.host}:{args.port}...")

        if not _wait_for_server(args.host, args.port, server_proc=server_proc):
            print("Error: Server failed to start within 30 seconds.")
            _kill_proc(server_proc)
            return 1

        print("Server ready.")
        print(f"Starting named tunnel '{tunnel_name}'...")
        tunnel_proc = _start_tunnel_named(tunnel_name)
        try:
            time.sleep(3)

            hostname = args.domain
            url = (
                f"https://{hostname}"
                if hostname
                else "(check Cloudflare dashboard for URL)"
            )

            _print_connection_info(url, is_permanent=True, api_key=api_key)
            return _run_processes(server_proc, tunnel_proc)
        except BaseException:
            _kill_proc(tunnel_proc)
            raise
    except BaseException:
        _kill_proc(server_proc)
        raise
