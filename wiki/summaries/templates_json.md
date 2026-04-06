# Template JSON Files

**Source:** `src/spicebridge/templates/*.json` (11 files)

## Purpose

Pre-built circuit templates shipped with SPICEBridge. Each JSON file defines a complete circuit topology with netlist, component descriptions, design equations, and port mappings.

## Available Templates

| File | ID | Category | Description |
|---|---|---|---|
| `rc_lowpass_1st.json` | `rc_lowpass_1st` | filters | 1st-order RC low-pass, -20 dB/dec |
| `rc_highpass_1st.json` | `rc_highpass_1st` | filters | 1st-order RC high-pass |
| `sallen_key_lowpass_2nd.json` | `sallen_key_lowpass_2nd` | filters | 2nd-order Sallen-Key low-pass |
| `sallen_key_hpf_2nd.json` | `sallen_key_hpf_2nd` | filters | 2nd-order Sallen-Key high-pass |
| `mfb_bandpass.json` | `mfb_bandpass` | filters | MFB bandpass filter |
| `twin_t_notch.json` | `twin_t_notch` | filters | Twin-T notch filter |
| `voltage_divider.json` | `voltage_divider` | basic | Resistive voltage divider |
| `inverting_opamp.json` | `inverting_opamp` | amplifiers | Inverting op-amp amplifier |
| `differential_amp.json` | `differential_amp` | amplifiers | Differential amplifier |
| `summing_amplifier.json` | `summing_amplifier` | amplifiers | Summing amplifier |
| `instrumentation_amp.json` | `instrumentation_amp` | amplifiers | Instrumentation amplifier |

## JSON Schema

Each template contains: `id` (string), `name` (string), `category` (string), `description` (string), `design_equations` (list of strings), `netlist` (SPICE netlist string with `.param` placeholders), `components` (dict of ref -> {description, default}), `ports` (optional dict of port_name -> node_name).

## FACTS

- All op-amp templates use a built-in ideal op-amp subcircuit with gain = 100,000 (Source: `inverting_opamp.json`).
- Component values use `.param` directives so they can be overridden by the solver without modifying instance lines (Source: `rc_lowpass_1st.json`).
- Port definitions follow the convention: `in` for input, `out` for output, `0` for ground.

## Architecture Role

Data layer. Loaded by [template_manager.py](template_manager.md). Values calculated by [solver.py](solver.md). See [template-system](../concepts/template-system.md).
