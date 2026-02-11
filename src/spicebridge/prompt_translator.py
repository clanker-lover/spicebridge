"""Prompt translator for local models.

Converts freeform English into structured JSON with intent, specs,
template match, and tool sequence — so local models (phi4, qwen via
Ollama) only have to execute and interpret.

Three-stage pipeline:
  1. Intent classification (weighted keyword scoring)
  2. Parameter extraction (regex-based with unit handling)
  3. Template matching & tool sequence generation

Usage:
    python -m spicebridge.prompt_translator "design a 1kHz low-pass filter"
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Stage 1 — Intent Classification
# ---------------------------------------------------------------------------

INTENTS = (
    "design_filter",
    "design_amplifier",
    "design_power",
    "design_oscillator",
    "modify_circuit",
    "analyze_circuit",
    "general_question",
)


@dataclass(frozen=True)
class _KeywordEntry:
    pattern: re.Pattern[str]
    weight: float


def _kw(text: str, weight: float) -> _KeywordEntry:
    """Build a keyword entry with a compiled case-insensitive pattern."""
    return _KeywordEntry(re.compile(re.escape(text), re.IGNORECASE), weight)


def _kw_re(pattern: str, weight: float) -> _KeywordEntry:
    """Build a keyword entry from a raw regex pattern."""
    return _KeywordEntry(re.compile(pattern, re.IGNORECASE), weight)


_INTENT_KEYWORDS: dict[str, list[_KeywordEntry]] = {
    "design_filter": [
        _kw("low-pass", 3),
        _kw_re(r"low\s*pass", 3),
        _kw("high-pass", 3),
        _kw_re(r"high\s*pass", 3),
        _kw("bandpass", 3),
        _kw_re(r"band\s*pass", 3),
        _kw("notch", 3),
        _kw("filter", 2),
        _kw("cutoff", 2),
        _kw("-3dB", 2),
        _kw_re(r"-3\s*dB", 2),
        _kw("sallen-key", 2),
        _kw_re(r"sallen\s*key", 2),
        _kw("mfb", 2),
        _kw("twin-t", 2),
        _kw_re(r"twin\s*t", 2),
        _kw("butterworth", 1.5),
    ],
    "design_amplifier": [
        _kw("amplifier", 2.5),
        _kw("inverting", 2.5),
        _kw("non-inverting", 3),
        _kw_re(r"non\s*inverting", 3),
        _kw("summing", 3),
        _kw("differential", 2.5),
        _kw("instrumentation", 3),
        _kw("op-amp", 2),
        _kw_re(r"op\s*amp", 2),
        _kw("buffer", 2),
        _kw("voltage-divider", 3),
        _kw_re(r"voltage\s+divider", 3),
        _kw("gain", 1.5),
    ],
    "design_power": [
        _kw("buck", 3),
        _kw("boost", 3),
        _kw("converter", 2),
        _kw("regulator", 2.5),
        _kw("power-supply", 3),
        _kw_re(r"power\s+supply", 3),
        _kw("LDO", 3),
    ],
    "design_oscillator": [
        _kw("oscillator", 3),
        _kw("Wien-bridge", 3),
        _kw_re(r"wien\s*bridge", 3),
        _kw("Colpitts", 3),
        _kw("crystal", 2),
        _kw("VCO", 3),
    ],
    "modify_circuit": [
        _kw("change", 2),
        _kw("modify", 2.5),
        _kw("replace", 2),
        _kw("update", 2),
        _kw("swap", 2),
        _kw_re(r"set\s+R\d+", 3),
        _kw_re(r"set\s+C\d+", 3),
    ],
    "analyze_circuit": [
        _kw("analyze", 2.5),
        _kw("analyse", 2.5),
        _kw("measure", 2.5),
        _kw("simulate", 2.5),
        _kw("bandwidth", 2),
        _kw("frequency-response", 2.5),
        _kw_re(r"frequency\s+response", 2.5),
    ],
}


def _classify_intent(text: str) -> tuple[str, float]:
    """Score *text* against each intent and return (intent, confidence)."""
    scores: dict[str, float] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        total = 0.0
        for entry in keywords:
            if entry.pattern.search(text):
                total += entry.weight
        if total > 0:
            scores[intent] = total

    if not scores:
        return "general_question", 0.0

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    if len(ranked) == 1:
        intent, score = ranked[0]
        confidence = min(1.0, score / 5.0)
        return intent, round(confidence, 4)

    top_intent, top_score = ranked[0]
    _, second_score = ranked[1]
    confidence = top_score / (top_score + second_score)
    return top_intent, round(confidence, 4)


# ---------------------------------------------------------------------------
# Stage 2 — Parameter Extraction
# ---------------------------------------------------------------------------

_UNIT_ALIASES: dict[str, str] = {
    # Frequency
    "hz": "Hz",
    "khz": "Hz",
    "mhz": "Hz",
    "ghz": "Hz",
    # Decibels
    "db": "dB",
    # Voltage
    "v": "V",
    "mv": "V",
    "kv": "V",
    # Current
    "a": "A",
    "ma": "A",
    "ua": "A",
    # Resistance
    "ohm": "Ohm",
    "ohms": "Ohm",
    "kohm": "Ohm",
    "kohms": "Ohm",
    "mohm": "Ohm",
    "mohms": "Ohm",
    "ω": "Ohm",
    "kω": "Ohm",
    "mω": "Ohm",
    # Capacitance
    "f": "F",
    "nf": "F",
    "uf": "F",
    "pf": "F",
    "µf": "F",
    # Inductance
    "h": "H",
    "mh": "H",
    "uh": "H",
    "µh": "H",
}

_UNIT_MULTIPLIERS: dict[str, float] = {
    # Frequency
    "hz": 1.0,
    "khz": 1e3,
    "mhz": 1e6,
    "ghz": 1e9,
    # Decibels
    "db": 1.0,
    # Voltage
    "v": 1.0,
    "mv": 1e-3,
    "kv": 1e3,
    # Current
    "a": 1.0,
    "ma": 1e-3,
    "ua": 1e-6,
    # Resistance
    "ohm": 1.0,
    "ohms": 1.0,
    "kohm": 1e3,
    "kohms": 1e3,
    "mohm": 1e6,
    "mohms": 1e6,
    "ω": 1.0,
    "kω": 1e3,
    "mω": 1e6,
    # Capacitance
    "f": 1.0,
    "nf": 1e-9,
    "uf": 1e-6,
    "pf": 1e-12,
    "µf": 1e-6,
    # Inductance
    "h": 1.0,
    "mh": 1e-3,
    "uh": 1e-6,
    "µh": 1e-6,
}

_NUMBER_UNIT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-zA-ZΩµ]+)", re.IGNORECASE)
_Q_RE = re.compile(r"\bQ\s*(?:=|of)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_RATIO_RE = re.compile(r"\bratio\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
_NUM_INPUTS_RE = re.compile(r"(\d+)\s*inputs?", re.IGNORECASE)
_GAIN_LINEAR_RE = re.compile(
    r"\bgain\s+(?:of\s+)?(\d+(?:\.\d+)?)\b(?!\s*[a-zA-Z])", re.IGNORECASE
)


def _extract_params(text: str, intent: str) -> tuple[dict[str, float], list[str]]:
    """Extract numerical parameters with units from *text*.

    Returns (specs, warnings).
    """
    specs: dict[str, float] = {}
    warnings: list[str] = []

    # Collect all (value, unit) pairs
    freq_values: list[float] = []
    db_values: list[float] = []
    volt_values: list[float] = []
    ohm_values: list[float] = []

    for m in _NUMBER_UNIT_RE.finditer(text):
        raw_val = float(m.group(1))
        raw_unit = m.group(2).strip()
        unit_lower = raw_unit.lower()

        base_unit = _UNIT_ALIASES.get(unit_lower)
        multiplier = _UNIT_MULTIPLIERS.get(unit_lower)

        if base_unit is None or multiplier is None:
            continue

        value = raw_val * multiplier

        if base_unit == "Hz":
            freq_values.append(value)
        elif base_unit == "dB":
            db_values.append(value)
        elif base_unit == "V":
            volt_values.append(value)
        elif base_unit == "A":
            pass  # not mapped to a spec currently
        elif base_unit == "Ohm":
            ohm_values.append(value)
        elif base_unit in ("F", "H"):
            pass  # not mapped to a spec currently

    # Map collected values to specs based on intent
    if intent == "design_filter":
        if freq_values:
            specs["_freq_hz"] = freq_values[0]
        if db_values:
            specs["gain_dB"] = db_values[0]
    elif intent == "design_amplifier":
        if db_values:
            specs["gain_dB"] = db_values[0]
        if ohm_values:
            specs["input_impedance_ohms"] = ohm_values[0]
        if len(volt_values) >= 2:
            sorted_v = sorted(volt_values, reverse=True)
            specs["input_voltage"] = sorted_v[0]
            specs["output_voltage"] = sorted_v[1]
        elif len(volt_values) == 1:
            # Single voltage — could be either; leave as generic
            pass
        if freq_values:
            specs["_freq_hz"] = freq_values[0]
    else:
        if freq_values:
            specs["_freq_hz"] = freq_values[0]
        if db_values:
            specs["gain_dB"] = db_values[0]
        if volt_values:
            if len(volt_values) >= 2:
                sorted_v = sorted(volt_values, reverse=True)
                specs["input_voltage"] = sorted_v[0]
                specs["output_voltage"] = sorted_v[1]
            else:
                specs["output_voltage"] = volt_values[0]
        if ohm_values:
            specs["input_impedance_ohms"] = ohm_values[0]

    # Context-sensitive extractions
    q_match = _Q_RE.search(text)
    if q_match:
        specs["Q"] = float(q_match.group(1))

    ratio_match = _RATIO_RE.search(text)
    if ratio_match:
        specs["ratio"] = float(ratio_match.group(1))

    inputs_match = _NUM_INPUTS_RE.search(text)
    if inputs_match:
        specs["num_inputs"] = float(inputs_match.group(1))

    # gain_linear: "gain of 5" without a unit suffix like dB
    if "gain_dB" not in specs:
        gl_match = _GAIN_LINEAR_RE.search(text)
        if gl_match:
            specs["gain_linear"] = float(gl_match.group(1))
    else:
        # Check if there's also a bare gain → warn
        gl_match = _GAIN_LINEAR_RE.search(text)
        if gl_match:
            warnings.append("Both gain_dB and gain_linear detected; using gain_dB.")

    return specs, warnings


# ---------------------------------------------------------------------------
# Stage 3 — Template Matching & Tool Sequence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TemplateInfo:
    template_id: str
    category: str
    required_specs: tuple[str, ...]
    optional_specs: tuple[str, ...]
    freq_spec_name: str | None
    has_template: bool
    sim_type: str


_TEMPLATE_REGISTRY: dict[str, _TemplateInfo] = {
    "rc_lowpass_1st": _TemplateInfo(
        template_id="rc_lowpass_1st",
        category="filters",
        required_specs=("f_cutoff_hz",),
        optional_specs=(),
        freq_spec_name="f_cutoff_hz",
        has_template=True,
        sim_type="ac",
    ),
    "rc_highpass_1st": _TemplateInfo(
        template_id="rc_highpass_1st",
        category="filters",
        required_specs=("f_cutoff_hz",),
        optional_specs=(),
        freq_spec_name="f_cutoff_hz",
        has_template=True,
        sim_type="ac",
    ),
    "sallen_key_lowpass_2nd": _TemplateInfo(
        template_id="sallen_key_lowpass_2nd",
        category="filters",
        required_specs=("f_cutoff_hz",),
        optional_specs=("Q",),
        freq_spec_name="f_cutoff_hz",
        has_template=True,
        sim_type="ac",
    ),
    "sallen_key_hpf_2nd": _TemplateInfo(
        template_id="sallen_key_hpf_2nd",
        category="filters",
        required_specs=("f_cutoff_hz",),
        optional_specs=("Q",),
        freq_spec_name="f_cutoff_hz",
        has_template=True,
        sim_type="ac",
    ),
    "mfb_bandpass": _TemplateInfo(
        template_id="mfb_bandpass",
        category="filters",
        required_specs=("f_center_hz",),
        optional_specs=("Q", "gain_linear"),
        freq_spec_name="f_center_hz",
        has_template=True,
        sim_type="ac",
    ),
    "twin_t_notch": _TemplateInfo(
        template_id="twin_t_notch",
        category="filters",
        required_specs=("f_notch_hz",),
        optional_specs=(),
        freq_spec_name="f_notch_hz",
        has_template=True,
        sim_type="ac",
    ),
    "inverting_opamp": _TemplateInfo(
        template_id="inverting_opamp",
        category="amplifiers",
        required_specs=("gain_dB",),
        optional_specs=("input_impedance_ohms",),
        freq_spec_name=None,
        has_template=True,
        sim_type="ac",
    ),
    "noninverting_opamp": _TemplateInfo(
        template_id="noninverting_opamp",
        category="amplifiers",
        required_specs=("gain_dB",),
        optional_specs=("input_impedance_ohms",),
        freq_spec_name=None,
        has_template=False,
        sim_type="ac",
    ),
    "summing_amplifier": _TemplateInfo(
        template_id="summing_amplifier",
        category="amplifiers",
        required_specs=(),
        optional_specs=("num_inputs", "gain_per_input", "input_impedance_ohms"),
        freq_spec_name=None,
        has_template=True,
        sim_type="ac",
    ),
    "differential_amp": _TemplateInfo(
        template_id="differential_amp",
        category="amplifiers",
        required_specs=(),
        optional_specs=("gain_linear", "input_impedance_ohms"),
        freq_spec_name=None,
        has_template=True,
        sim_type="ac",
    ),
    "instrumentation_amp": _TemplateInfo(
        template_id="instrumentation_amp",
        category="amplifiers",
        required_specs=("gain_linear",),
        optional_specs=("r_bridge",),
        freq_spec_name=None,
        has_template=True,
        sim_type="ac",
    ),
    "voltage_divider": _TemplateInfo(
        template_id="voltage_divider",
        category="basic",
        required_specs=(),
        optional_specs=("ratio", "input_voltage", "output_voltage"),
        freq_spec_name=None,
        has_template=True,
        sim_type="dc",
    ),
}

# Filter template lookup: (filter_type, order) -> template_id
_FILTER_TEMPLATE_MAP: dict[tuple[str, int], str] = {
    ("lowpass", 1): "rc_lowpass_1st",
    ("lowpass", 2): "sallen_key_lowpass_2nd",
    ("highpass", 1): "rc_highpass_1st",
    ("highpass", 2): "sallen_key_hpf_2nd",
    ("bandpass", 1): "mfb_bandpass",
    ("bandpass", 2): "mfb_bandpass",
    ("notch", 1): "twin_t_notch",
    ("notch", 2): "twin_t_notch",
}

_FILTER_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("lowpass", re.compile(r"low[\s-]?pass", re.IGNORECASE)),
    ("highpass", re.compile(r"high[\s-]?pass", re.IGNORECASE)),
    ("bandpass", re.compile(r"band[\s-]?pass", re.IGNORECASE)),
    ("notch", re.compile(r"notch", re.IGNORECASE)),
]

_SECOND_ORDER_RE = re.compile(
    r"2nd\s*order|second\s*order|sallen|butterworth", re.IGNORECASE
)

# Amplifier matching — priority ordered
_AMP_MATCH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("voltage_divider", re.compile(r"voltage[\s-]?divider", re.IGNORECASE)),
    ("instrumentation_amp", re.compile(r"instrumentation", re.IGNORECASE)),
    ("summing_amplifier", re.compile(r"summing", re.IGNORECASE)),
    ("differential_amp", re.compile(r"differential", re.IGNORECASE)),
    ("noninverting_opamp", re.compile(r"non[\s-]?inverting|buffer", re.IGNORECASE)),
    ("inverting_opamp", re.compile(r"inverting", re.IGNORECASE)),
]

# Tool sequences per intent / template
_TOOL_SEQUENCES: dict[str, list[str]] = {
    "design_filter": [
        "load_template",
        "validate_netlist",
        "run_ac_analysis",
        "measure_bandwidth",
        "compare_specs",
        "draw_schematic",
    ],
    "design_amplifier": [
        "load_template",
        "validate_netlist",
        "run_dc_op",
        "run_ac_analysis",
        "measure_gain",
        "compare_specs",
        "draw_schematic",
    ],
    "voltage_divider": [
        "load_template",
        "validate_netlist",
        "run_dc_op",
        "measure_dc",
        "compare_specs",
        "draw_schematic",
    ],
    "modify_circuit": ["modify_component"],
    "analyze_circuit": [
        "run_ac_analysis",
        "measure_bandwidth",
        "measure_gain",
        "get_results",
    ],
    "escalate": ["escalate"],
    "general_question": [],
}


def _match_filter(text: str, specs: dict[str, float]) -> tuple[str | None, int]:
    """Detect filter sub-type and order from *text*."""
    filter_type: str | None = None
    for ft, pat in _FILTER_TYPE_PATTERNS:
        if pat.search(text):
            filter_type = ft
            break

    if filter_type is None:
        # Fallback: default to lowpass if it's a design_filter intent
        filter_type = "lowpass"

    # Detect order
    order = 1
    if _SECOND_ORDER_RE.search(text) or "Q" in specs:
        order = 2

    return filter_type, order


def _match_amplifier(text: str, specs: dict[str, float]) -> str:
    """Detect amplifier sub-type from *text*."""
    for template_id, pat in _AMP_MATCH_PATTERNS:
        if pat.search(text):
            return template_id

    # Default: if gain present, inverting_opamp
    if "gain_dB" in specs or "gain_linear" in specs:
        return "inverting_opamp"

    return "inverting_opamp"


def _resolve_template(
    intent: str, text: str, specs: dict[str, float]
) -> tuple[str | None, dict[str, float], list[str]]:
    """Resolve template_id and finalize specs.

    Returns (template_id, updated_specs, tool_sequence).
    """
    if intent == "design_filter":
        filter_type, order = _match_filter(text, specs)
        key = (filter_type, order)
        template_id = _FILTER_TEMPLATE_MAP.get(key)
        if template_id and template_id in _TEMPLATE_REGISTRY:
            info = _TEMPLATE_REGISTRY[template_id]
            # Rename _freq_hz to the template's correct spec name
            if "_freq_hz" in specs and info.freq_spec_name:
                specs[info.freq_spec_name] = specs.pop("_freq_hz")
            elif "_freq_hz" in specs:
                specs.pop("_freq_hz")
        tools = _TOOL_SEQUENCES["design_filter"]
        return template_id, specs, tools

    if intent == "design_amplifier":
        template_id = _match_amplifier(text, specs)
        if template_id in _TEMPLATE_REGISTRY:
            info = _TEMPLATE_REGISTRY[template_id]
            if "_freq_hz" in specs and info.freq_spec_name:
                specs[info.freq_spec_name] = specs.pop("_freq_hz")
            elif "_freq_hz" in specs:
                specs.pop("_freq_hz")
        if template_id == "voltage_divider":
            tools = _TOOL_SEQUENCES["voltage_divider"]
        else:
            tools = _TOOL_SEQUENCES["design_amplifier"]
        return template_id, specs, tools

    if intent in ("design_power", "design_oscillator"):
        if "_freq_hz" in specs:
            specs.pop("_freq_hz")
        return None, specs, _TOOL_SEQUENCES["escalate"]

    if intent == "modify_circuit":
        if "_freq_hz" in specs:
            specs.pop("_freq_hz")
        return None, specs, _TOOL_SEQUENCES["modify_circuit"]

    if intent == "analyze_circuit":
        if "_freq_hz" in specs:
            specs.pop("_freq_hz")
        return None, specs, _TOOL_SEQUENCES["analyze_circuit"]

    # general_question
    if "_freq_hz" in specs:
        specs.pop("_freq_hz")
    return None, specs, _TOOL_SEQUENCES["general_question"]


# ---------------------------------------------------------------------------
# Formatted prompt generation
# ---------------------------------------------------------------------------


def _build_formatted_prompt(
    intent: str,
    template_id: str | None,
    specs: dict[str, float],
    missing_required: list[str],
    original_input: str,
) -> str:
    """Build a compact instruction string for the local model."""
    parts: list[str] = []

    if intent in ("design_power", "design_oscillator"):
        parts.append(
            f'[ESCALATE TO CLOUD] User requested: "{original_input}"'
            " — no available template."
        )
    elif template_id and template_id in _TEMPLATE_REGISTRY:
        info = _TEMPLATE_REGISTRY[template_id]
        if not info.has_template:
            # Solver-only (e.g. noninverting_opamp)
            specs_json = json.dumps(specs)
            parts.append(
                f'Use calculate_components with topology_id="{template_id}"'
                f", specs={specs_json}."
            )
        else:
            specs_json = json.dumps(specs)
            parts.append(
                f'Use auto_design with template_id="{template_id}"'
                f', specs={specs_json}, sim_type="{info.sim_type}".'
            )
    elif template_id is None and intent not in (
        "general_question",
        "modify_circuit",
        "analyze_circuit",
    ):
        parts.append(
            f'[ESCALATE TO CLOUD] User requested: "{original_input}"'
            " — no available template."
        )

    if missing_required:
        parts.append(
            f"Note: missing required specs: {missing_required}."
            " Ask the user for these values."
        )

    parts.append(f'User\'s original request: "{original_input}"')

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def translate_prompt(user_input: str) -> dict:
    """Translate freeform English into a structured design dict.

    Returns a dict with keys:
        intent, confidence, specs, template_id, has_template, sim_type,
        tool_sequence, formatted_prompt, missing_required, warnings,
        original_input
    """
    text = user_input.strip()

    # Stage 1 — Intent Classification
    intent, confidence = _classify_intent(text)

    # Stage 2 — Parameter Extraction
    specs, warnings = _extract_params(text, intent)

    # Stage 3 — Template Matching & Tool Sequence
    template_id, specs, tool_sequence = _resolve_template(intent, text, specs)

    # Determine template info
    has_template: bool | None = None
    sim_type: str | None = None
    if template_id and template_id in _TEMPLATE_REGISTRY:
        info = _TEMPLATE_REGISTRY[template_id]
        has_template = info.has_template
        sim_type = info.sim_type

    # Check for missing required specs
    missing_required: list[str] = []
    if template_id and template_id in _TEMPLATE_REGISTRY:
        info = _TEMPLATE_REGISTRY[template_id]
        for req in info.required_specs:
            if req not in specs:
                missing_required.append(req)

    # Build formatted prompt
    formatted_prompt = _build_formatted_prompt(
        intent, template_id, specs, missing_required, text
    )

    return {
        "intent": intent,
        "confidence": confidence,
        "specs": specs,
        "template_id": template_id,
        "has_template": has_template,
        "sim_type": sim_type,
        "tool_sequence": tool_sequence,
        "formatted_prompt": formatted_prompt,
        "missing_required": missing_required,
        "warnings": warnings,
        "original_input": text,
    }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m spicebridge.prompt_translator <prompt>")
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])
    result = translate_prompt(prompt)
    print(json.dumps(result, indent=2))
