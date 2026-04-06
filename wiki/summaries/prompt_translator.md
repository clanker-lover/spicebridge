# prompt_translator.py

**Source:** `src/spicebridge/prompt_translator.py`

## Purpose

Converts freeform English circuit design requests into structured JSON with intent, specs, template match, and tool sequence. Designed to pre-process prompts so that local models (phi4, qwen via Ollama) only need to execute and interpret -- not understand circuit design vocabulary.

## Public API

- **`translate_prompt(user_input)`**: Main entry point. Returns dict with `intent`, `confidence`, `specs`, `template_id`, `has_template`, `sim_type`, `tool_sequence`, `formatted_prompt`, `missing_required`, `warnings`, `original_input`.

## Three-Stage Pipeline

**Stage 1 -- Intent Classification**: Weighted keyword scoring across 7 intents: `design_filter`, `design_amplifier`, `design_power`, `design_oscillator`, `modify_circuit`, `analyze_circuit`, `general_question`. Confidence = top_score / (top_score + second_score).

**Stage 2 -- Parameter Extraction**: Regex-based extraction of numbers with units. Handles frequency (Hz/kHz/MHz), decibels, voltage, resistance, capacitance, inductance. Also extracts Q factor, ratio, num_inputs, gain_linear. Maps values to spec keys based on intent.

**Stage 3 -- Template Matching**: Maps (filter_type, order) to template IDs for filters. Matches amplifier sub-types by priority-ordered regex patterns. Generates tool sequence per intent. Unsupported topologies (power, oscillator) produce `[ESCALATE TO CLOUD]`.

## CLI Usage

`python -m spicebridge.prompt_translator "design a 1kHz low-pass filter"` -- outputs JSON.

## Dependencies

`re`, `json`, `sys`. No spicebridge imports.

## Architecture Role

NLP preprocessing layer. Standalone module usable outside the MCP server. See [prompt-translation](../concepts/prompt-translation.md).
