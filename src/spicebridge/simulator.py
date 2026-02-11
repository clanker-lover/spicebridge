"""Run ngspice simulations via spicelib or direct subprocess fallback."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 — used with list args, no shell=True
import tempfile
from pathlib import Path

_ngspice_available: bool | None = None


def _check_ngspice() -> bool:
    """Check whether ngspice is available on PATH (result is cached)."""
    global _ngspice_available  # noqa: PLW0603
    if _ngspice_available is None:
        _ngspice_available = shutil.which("ngspice") is not None
    return _ngspice_available


def _run_via_spicelib(netlist_file: Path, raw_file: Path) -> bool:
    """Attempt simulation using spicelib's NGspiceSimulator."""
    try:
        from spicelib.simulators.ngspice_simulator import NGspiceSimulator

        NGspiceSimulator.run(str(netlist_file))
        return raw_file.exists() and raw_file.stat().st_size > 0
    except Exception:
        return False


def _run_via_subprocess(netlist_file: Path, raw_file: Path) -> bool:
    """Run ngspice directly via subprocess as a fallback."""
    try:
        result = subprocess.run(  # nosec B603 B607 — list args, no shell, trusted binary
            ["ngspice", "-b", "-r", str(raw_file), str(netlist_file)],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False
        return raw_file.exists() and raw_file.stat().st_size > 0
    except Exception:
        return False


def run_simulation(netlist: str, output_dir: str | Path | None = None) -> bool:
    """Run an ngspice simulation on the given netlist string.

    Parameters
    ----------
    netlist : str
        Complete SPICE netlist including analysis commands and .end
    output_dir : str | Path | None
        Directory for output files. A temp directory is created if None.

    Returns
    -------
    bool
        True if simulation produced a non-empty .raw file.

    Raises
    ------
    RuntimeError
        If ngspice is not installed / not on PATH.
    """
    if not _check_ngspice():
        raise RuntimeError(
            "ngspice is not installed or not on PATH. "
            "Install it with: sudo apt install ngspice"
        )

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="spicebridge_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    netlist_file = output_dir / "circuit.net"
    raw_file = output_dir / "circuit.raw"
    netlist_file.write_text(netlist)

    # Try spicelib first, fall back to direct subprocess
    if _run_via_spicelib(netlist_file, raw_file):
        return True
    return _run_via_subprocess(netlist_file, raw_file)


def validate_netlist_syntax(netlist: str) -> tuple[bool, list[str]]:
    """Check a netlist for syntax errors by running ngspice in batch mode.

    Returns
    -------
    tuple[bool, list[str]]
        (is_valid, error_messages) — *is_valid* is True when ngspice
        reports no errors; *error_messages* collects lines containing
        "error" or "fatal".
    """
    if not _check_ngspice():
        raise RuntimeError(
            "ngspice is not installed or not on PATH. "
            "Install it with: sudo apt install ngspice"
        )

    tmp = Path(tempfile.mkdtemp(prefix="spicebridge_validate_"))
    netlist_file = tmp / "check.net"
    netlist_file.write_text(netlist)

    try:
        result = subprocess.run(  # nosec B603 B607 — list args, no shell, trusted binary
            ["ngspice", "-b", str(netlist_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, ["ngspice timed out"]

    errors: list[str] = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        lower = line.lower()
        if "error" in lower or "fatal" in lower:
            errors.append(line.strip())

    return (len(errors) == 0, errors)
