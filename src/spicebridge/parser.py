"""SPICE output parser — extract structured metrics from ngspice .raw files."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from spicelib import RawRead


def detect_analysis_type(raw_path: str | Path) -> str:
    """Detect the analysis type from a .raw file.

    Returns one of: "AC Analysis", "Transient Analysis", "Operating Point".
    """
    raw = RawRead(str(raw_path), dialect="ngspice")
    return raw.get_plot_name()


def _select_output_trace(trace_names: list[str]) -> str:
    """Pick the best output trace from available trace names.

    Priority: v(out) > first v(...) not in {v(in), v(v1)} > first trace.
    """
    skip = {"v(in)", "v(v1)"}
    lower_names = [n.lower() for n in trace_names]

    if "v(out)" in lower_names:
        return trace_names[lower_names.index("v(out)")]

    for i, name in enumerate(lower_names):
        if name.startswith("v(") and name.endswith(")") and name not in skip:
            return trace_names[i]

    return trace_names[0]


def parse_ac(raw_path: str | Path) -> dict:
    """Parse AC analysis results from a .raw file."""
    raw = RawRead(str(raw_path), dialect="ngspice")
    trace_names = raw.get_trace_names()
    output_trace = _select_output_trace(trace_names)

    freq_data = raw.get_trace("frequency").get_wave(0)
    freqs = np.real(freq_data)

    data = raw.get_trace(output_trace).get_wave(0)
    mag_db = 20 * np.log10(np.abs(data) + 1e-20)
    phase = np.angle(data, deg=True)

    # DC gain (first point)
    gain_dc_db = float(mag_db[0])

    # Peak gain
    peak_idx = int(np.argmax(mag_db))
    peak_gain_db = float(mag_db[peak_idx])
    peak_gain_freq = float(freqs[peak_idx])

    # f_3dB: first frequency where gain drops 3 dB below DC gain
    threshold = gain_dc_db - 3.0
    f_3db = None
    phase_at_f3db = None
    rolloff_rate = None

    below = np.where(mag_db < threshold)[0]
    if len(below) > 0:
        idx = below[0]
        if idx > 0:
            # Linear interpolation between idx-1 and idx
            f_3db = float(
                np.interp(
                    threshold,
                    [mag_db[idx], mag_db[idx - 1]],
                    [freqs[idx], freqs[idx - 1]],
                )
            )
            phase_at_f3db = float(
                np.interp(
                    f_3db, [freqs[idx - 1], freqs[idx]], [phase[idx - 1], phase[idx]]
                )
            )
            # Rolloff: dB difference over one decade above f_3dB
            f_decade = f_3db * 10
            if f_decade <= freqs[-1]:
                gain_at_decade = float(np.interp(f_decade, freqs, mag_db))
                rolloff_rate = gain_at_decade - float(np.interp(f_3db, freqs, mag_db))
        else:
            f_3db = float(freqs[0])
            phase_at_f3db = float(phase[0])

    return {
        "analysis_type": "AC Analysis",
        "traces": list(trace_names),
        "f_3dB_hz": f_3db,
        "gain_dc_dB": gain_dc_db,
        "rolloff_rate_dB_per_decade": rolloff_rate,
        "phase_at_f3dB_deg": phase_at_f3db,
        "peak_gain_dB": peak_gain_db,
        "peak_gain_freq_hz": peak_gain_freq,
        "num_points": len(freqs),
        "freq_range": [float(freqs[0]), float(freqs[-1])],
    }


def parse_transient(raw_path: str | Path) -> dict:
    """Parse transient analysis results from a .raw file."""
    raw = RawRead(str(raw_path), dialect="ngspice")
    trace_names = raw.get_trace_names()
    output_trace = _select_output_trace(trace_names)

    time = raw.get_trace("time").get_wave(0)
    voltage = raw.get_trace(output_trace).get_wave(0)

    time = np.real(time)
    voltage = np.real(voltage)

    # Steady state: mean of last 10%
    n_last = max(1, len(voltage) // 10)
    steady_state = float(np.mean(voltage[-n_last:]))
    peak_value = float(np.max(voltage))

    # Rise time: 10% to 90% of steady state
    rise_time = None
    if steady_state != 0:
        thresh_10 = 0.1 * steady_state
        thresh_90 = 0.9 * steady_state
        cross_10 = np.where(voltage >= thresh_10)[0]
        cross_90 = np.where(voltage >= thresh_90)[0]
        if len(cross_10) > 0 and len(cross_90) > 0:
            rise_time = float(time[cross_90[0]] - time[cross_10[0]])

    # Overshoot
    overshoot = None
    if steady_state > 0:
        overshoot = float((peak_value - steady_state) / steady_state * 100)

    # Settling time: first time after which all remaining points stay within 1%
    settling_time = None
    if steady_state != 0:
        tolerance = abs(steady_state) * 0.01
        within = np.abs(voltage - steady_state) <= tolerance
        # Walk backwards to find last out-of-tolerance point
        for i in range(len(within) - 1, -1, -1):
            if not within[i]:
                if i + 1 < len(time):
                    settling_time = float(time[i + 1])
                break

    return {
        "analysis_type": "Transient Analysis",
        "traces": list(trace_names),
        "steady_state_value": steady_state,
        "peak_value": peak_value,
        "rise_time_10_90_s": rise_time,
        "overshoot_pct": overshoot,
        "settling_time_1pct_s": settling_time,
        "num_points": len(time),
        "time_range": [float(time[0]), float(time[-1])],
    }


def parse_dc_op(raw_path: str | Path) -> dict:
    """Parse DC operating point results from a .raw file."""
    raw = RawRead(str(raw_path), dialect="ngspice")
    trace_names = raw.get_trace_names()

    nodes = {}
    for name in trace_names:
        nodes[name] = float(raw.get_trace(name).get_wave(0)[0])

    return {
        "analysis_type": "Operating Point",
        "nodes": nodes,
        "num_nodes": len(nodes),
    }


def read_ac_at_frequency(raw_path: str | Path, frequency_hz: float) -> dict:
    """Read AC analysis .raw file and interpolate gain/phase at a specific frequency.

    Returns {"gain_db": float, "phase_deg": float}.
    Raises ValueError if frequency is outside the simulated range.
    """
    raw = RawRead(str(raw_path), dialect="ngspice")
    trace_names = raw.get_trace_names()
    output_trace = _select_output_trace(trace_names)

    freq_data = raw.get_trace("frequency").get_wave(0)
    freqs = np.real(freq_data)

    if frequency_hz < freqs[0] or frequency_hz > freqs[-1]:
        raise ValueError(
            f"Frequency {frequency_hz} Hz is outside simulated range "
            f"[{float(freqs[0])}, {float(freqs[-1])}] Hz"
        )

    data = raw.get_trace(output_trace).get_wave(0)
    mag_db = 20 * np.log10(np.abs(data) + 1e-20)
    phase = np.angle(data, deg=True)

    gain_db = float(np.interp(frequency_hz, freqs, mag_db))
    phase_deg = float(np.interp(frequency_hz, freqs, phase))

    return {"gain_db": gain_db, "phase_deg": phase_deg}


def read_ac_bandwidth(raw_path: str | Path, threshold_db: float) -> dict:
    """Read AC analysis .raw file and find cutoff at an arbitrary threshold.

    threshold_db should be negative (e.g., -6). The target level is
    gain_dc_db + threshold_db.

    Returns {"f_cutoff_hz": float|None, "rolloff_db_per_decade": float|None}.
    """
    raw = RawRead(str(raw_path), dialect="ngspice")
    trace_names = raw.get_trace_names()
    output_trace = _select_output_trace(trace_names)

    freq_data = raw.get_trace("frequency").get_wave(0)
    freqs = np.real(freq_data)

    data = raw.get_trace(output_trace).get_wave(0)
    mag_db = 20 * np.log10(np.abs(data) + 1e-20)

    gain_dc_db = float(mag_db[0])
    target = gain_dc_db + threshold_db

    f_cutoff = None
    rolloff_rate = None

    below = np.where(mag_db < target)[0]
    if len(below) > 0:
        idx = below[0]
        if idx > 0:
            f_cutoff = float(
                np.interp(
                    target,
                    [mag_db[idx], mag_db[idx - 1]],
                    [freqs[idx], freqs[idx - 1]],
                )
            )
            f_decade = f_cutoff * 10
            if f_decade <= freqs[-1]:
                gain_at_decade = float(np.interp(f_decade, freqs, mag_db))
                rolloff_rate = gain_at_decade - float(
                    np.interp(f_cutoff, freqs, mag_db)
                )
        else:
            f_cutoff = float(freqs[0])

    return {"f_cutoff_hz": f_cutoff, "rolloff_db_per_decade": rolloff_rate}


def parse_results(raw_path: str | Path) -> dict:
    """Detect analysis type and parse results accordingly."""
    analysis = detect_analysis_type(raw_path)

    if "AC" in analysis:
        return parse_ac(raw_path)
    elif "Transient" in analysis:
        return parse_transient(raw_path)
    elif "Operating Point" in analysis:
        return parse_dc_op(raw_path)
    else:
        raise ValueError(f"Unknown analysis type: {analysis}")
