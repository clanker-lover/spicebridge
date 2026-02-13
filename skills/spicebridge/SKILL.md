---
name: spicebridge
description: >
  Circuit design and SPICE simulation assistant. Use when the user asks about
  circuit design, filters (low-pass, high-pass, bandpass, notch), amplifiers
  (inverting, differential, instrumentation, summing), voltage dividers,
  SPICE simulation, AC/DC/transient analysis, schematics, component selection,
  frequency response, gain, bandwidth, rolloff, phase margin, Monte Carlo
  tolerance analysis, or KiCad export.
user-invocable: false
---

# SPICEBridge Circuit Design

You have access to SPICEBridge, a full SPICE simulation toolchain exposed via MCP.
Use it to design, simulate, verify, and visualize analog circuits.

## Recommended Workflow

Follow this order for every design request:

1. **Identify the topology.** Match the user's request to a template if one exists. Use `list_templates` if unsure.
2. **Design and simulate in one shot.** Call `auto_design` with the template ID and target specs. This loads the template, solves component values, runs simulation, and checks specs automatically.
3. **Draw the schematic.** Call `draw_schematic` and always share the `schematic_url` link with the user. The user cannot see inline images.
4. **Verify specs.** Review the `comparison` section from `auto_design` results. If any spec failed, adjust components with `modify_component` and re-simulate.
5. **Offer Monte Carlo analysis** for production designs. Run `run_monte_carlo` with realistic tolerances (5% for resistors, 10% for ceramics, 5% for film capacitors) to show yield.

If no template fits, write a SPICE netlist manually with `create_circuit`, then simulate with the appropriate analysis tool.

## Available Templates

### Filters

| Template ID | Type | Order | Rolloff | Use When |
|---|---|---|---|---|
| `rc_lowpass_1st` | Low-pass | 1st | -20 dB/dec | Simple anti-alias, DC smoothing, gentle rolloff is acceptable |
| `rc_highpass_1st` | High-pass | 1st | +20 dB/dec | DC blocking, simple bass cut |
| `sallen_key_lowpass_2nd` | Low-pass | 2nd | -40 dB/dec | Sharper cutoff needed, Butterworth flatness desired |
| `sallen_key_hpf_2nd` | High-pass | 2nd | -40 dB/dec | Sharper high-pass with flat passband |
| `mfb_bandpass` | Bandpass | 2nd | -- | Selecting a specific frequency band, tunable Q and gain |
| `twin_t_notch` | Notch | 2nd | -- | Rejecting a single frequency (50/60 Hz hum, interference) |

### Amplifiers

| Template ID | Type | Use When |
|---|---|---|
| `inverting_opamp` | Inverting amp | Simple gain stage, known gain ratio, input impedance = Rin |
| `differential_amp` | Differential amp | Amplifying difference between two signals, rejecting common-mode |
| `instrumentation_amp` | Instrumentation amp | High input impedance needed, gain set by single resistor Rg |
| `summing_amplifier` | Summing amp | Mixing multiple signals with weighted sum |

### Basic

| Template ID | Type | Use When |
|---|---|---|
| `voltage_divider` | Voltage divider | Simple resistive voltage scaling, biasing |

## Spec Format for auto_design

Specs use this format:
```json
{"f_3dB_hz": {"target": 1000, "tolerance_pct": 10}}
```

Or min/max bounds:
```json
{"gain_db": {"min": 19, "max": 21}}
```

Common spec keys: `f_3dB_hz`, `gain_db`, `phase_deg`, `dc_voltage`.

## Key Design Gotchas

### E24 Rounding
Component values are automatically snapped to the E24 standard series (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1 and decade multiples). This means the actual cutoff or gain will differ slightly from the exact target. Always verify with simulation after snapping.

### First-Order vs Second-Order Filters
- **First-order** (RC): -20 dB/decade rolloff, gentle transition. Use when simplicity matters or spec is loose.
- **Second-order** (Sallen-Key, MFB): -40 dB/decade rolloff, sharper knee. Use when the user says "sharp cutoff", "Butterworth", or needs better stopband rejection.
- If the user just says "low-pass filter" without specifics, start with first-order. Mention second-order as an option.

### Passive vs Active Filters
- **Passive** (RC only): no power supply needed, no gain, signal attenuation in passband. Templates: `rc_lowpass_1st`, `rc_highpass_1st`.
- **Active** (opamp-based): can provide gain, better loaded performance, needs power supply. Templates: `sallen_key_*`, `mfb_bandpass`, `twin_t_notch`.
- For audio or precision applications, prefer active. For simple signal conditioning, passive is fine.

### Impedance Considerations
- Keep resistor values between 1k and 100k for most designs. Below 1k draws excessive current; above 100k picks up noise.
- For capacitors, prefer values between 100pF and 10uF. Smaller values are sensitive to parasitics; larger electrolytics have poor frequency response.

## Interpreting Results

### AC Analysis
- **-3 dB point**: the cutoff frequency where output power is half the passband value. This is the standard bandwidth definition.
- **Rolloff rate**: first-order = -20 dB/decade (-6 dB/octave), second-order = -40 dB/decade (-12 dB/octave).
- **Phase at cutoff**: first-order filter has -45 deg (low-pass) or +45 deg (high-pass) at f_c.
- **Gain in dB**: 20 * log10(Vout/Vin). Positive = amplification, negative = attenuation. 6 dB is roughly 2x voltage.

### Transient Analysis
- **Rise time**: 10% to 90% of final value. Related to bandwidth by t_r * BW ~ 0.35.
- **Overshoot**: percentage above final value. Higher Q = more overshoot. Butterworth (Q=0.707) has ~4% overshoot.
- **Settling time**: time to reach and stay within a tolerance band (usually 2% or 5%) of final value.

### DC Operating Point
- Check node voltages to verify biasing. Opamp outputs should not be near the supply rails.
- Use `measure_dc` to read specific node voltages. Use `measure_power` for power consumption.

## Multi-Stage Design

Use `connect_stages` to chain circuits together. Example: input filter followed by amplifier. Stages are auto-wired (out of stage N to in of stage N+1) and ground is shared.

## Schematic URLs

When any tool returns a `schematic_url`, you MUST include it as a clickable markdown link in your response. The user cannot see inline images or tool result data. The URL is the only way they can view the schematic.

## Monte Carlo and Worst-Case Analysis

For production readiness:
- `run_monte_carlo`: randomized component variations over N runs. Shows statistical spread of performance. Use 5% tolerance for resistors, 10% for ceramic capacitors.
- `run_worst_case`: deterministic corner analysis. Finds the true worst-case performance bounds. Better for go/no-go decisions.

Present Monte Carlo results as: nominal value, mean, standard deviation, min, max, and yield percentage.
