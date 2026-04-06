# Prompt Translation

Cross-cutting concept appearing in: `prompt_translator.py`, `prompts/local_system_prompt.md`, `prompts/cloud_system_prompt.md`

## FACTS

SPICEBridge has a three-tier AI integration strategy (Source: `prompt_translator.py`, prompt files):

### Tier 1: Cloud AI (Claude)
Uses the full cloud system prompt (`prompts/cloud_system_prompt.md`). Claude has enough intelligence to interpret tool results, select topologies, and iterate on designs. The SKILL.md auto-triggers on circuit-related keywords.

### Tier 2: Local AI with Translator
For local models (phi4, qwen via Ollama) that lack circuit design knowledge, `prompt_translator.py` pre-processes English into structured JSON (Source: `prompt_translator.py`). The local model only needs to execute the prescribed tool sequence and format results. The local system prompt (`prompts/local_system_prompt.md`) is deliberately terse: "Report numbers, not descriptions."

### Tier 3: Escalation
Unsupported topologies (power supplies, oscillators) produce `[ESCALATE TO CLOUD]` markers, signaling that a more capable model is needed (Source: `prompt_translator.py`).

### Translation Pipeline
1. **Intent classification**: Weighted keyword scoring across 7 intent categories. Confidence derived from score ratio between top two candidates.
2. **Parameter extraction**: Regex-based number+unit extraction with SI prefix handling. Context-sensitive mapping (frequencies -> filter specs, dB -> gain specs).
3. **Template matching**: Intent-specific resolution (filter_type + order -> template_id, amplifier sub-type detection).

## INFERENCES

This is a "vibe-CAD" architecture -- natural language in, working circuit out. The translator eliminates the need for local models to understand circuit design; they just execute a pre-computed tool sequence. The escalation pattern enables graceful degradation across model capability tiers.

## Related Pages

- [prompt_translator.py](../summaries/prompt_translator.md), [prompt files](../summaries/prompt_files.md)
- [template-system](template-system.md) -- templates the translator selects
