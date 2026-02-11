"""Run ngspice simulations via spicelib or direct subprocess fallback."""

from __future__ import annotations

import concurrent.futures
import logging
import shutil
import subprocess  # nosec B404 — used with list args, no shell=True
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_SIMULATION_TIMEOUT = 60
_MAX_CONCURRENT_SIMS = 4
_sim_semaphore = threading.Semaphore(_MAX_CONCURRENT_SIMS)


def _check_ngspice() -> bool:
    """Check whether ngspice is available on PATH."""
    return shutil.which("ngspice") is not None


def _run_via_spicelib(netlist_file: Path, raw_file: Path) -> bool:
    """Attempt simulation using spicelib's NGspiceSimulator."""
    try:
        from spicelib.simulators.ngspice_simulator import NGspiceSimulator

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(NGspiceSimulator.run, str(netlist_file))
            future.result(timeout=_SIMULATION_TIMEOUT)
        return raw_file.exists() and raw_file.stat().st_size > 0
    except ImportError:
        logger.debug("spicelib not installed, skipping spicelib backend")
        return False
    except concurrent.futures.TimeoutError:
        logger.debug("spicelib simulation timed out after %ss", _SIMULATION_TIMEOUT)
        return False
    except (OSError, RuntimeError) as exc:
        logger.debug("spicelib simulation failed: %s", exc)
        return False


def _run_via_subprocess(netlist_file: Path, raw_file: Path) -> bool:
    """Run ngspice directly via subprocess as a fallback."""
    try:
        result = subprocess.run(  # nosec B603 B607 — list args, no shell, trusted binary
            ["ngspice", "-b", "-r", str(raw_file), str(netlist_file)],
            capture_output=True,
            timeout=_SIMULATION_TIMEOUT,
        )
        if result.returncode != 0:
            logger.debug(
                "ngspice exited with code %d: %s",
                result.returncode,
                result.stderr[:500] if result.stderr else "",
            )
            return False
        return raw_file.exists() and raw_file.stat().st_size > 0
    except subprocess.TimeoutExpired:
        logger.debug("ngspice subprocess timed out after %ss", _SIMULATION_TIMEOUT)
        return False
    except OSError as exc:
        logger.debug("ngspice subprocess failed: %s", exc)
        return False


def _do_simulation(netlist: str, output_dir: Path) -> bool:
    """Write netlist to output_dir and run simulation. Returns True on success."""
    netlist_file = output_dir / "circuit.net"
    raw_file = output_dir / "circuit.raw"
    netlist_file.write_text(netlist)

    # Try spicelib first, fall back to direct subprocess
    if _run_via_spicelib(netlist_file, raw_file):
        return True
    return _run_via_subprocess(netlist_file, raw_file)


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

    _sim_semaphore.acquire()
    try:
        if output_dir is None:
            with tempfile.TemporaryDirectory(prefix="spicebridge_") as tmpdir:
                return _do_simulation(netlist, Path(tmpdir))
        else:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            return _do_simulation(netlist, output_dir)
    finally:
        _sim_semaphore.release()


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

    with tempfile.TemporaryDirectory(prefix="spicebridge_validate_") as tmpdir:
        tmp = Path(tmpdir)
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
