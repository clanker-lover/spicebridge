# System Prompts

**Sources:** `prompts/cloud_system_prompt.md`, `prompts/local_system_prompt.md`

## Purpose

Two system prompts that instruct AI clients how to use SPICEBridge tools effectively.

## cloud_system_prompt.md

FACTS: Full-featured prompt for cloud-capable AI clients (Claude). 126 lines. Contains:
- The 10-step design loop workflow (understand -> select -> calculate -> validate -> DC bias -> simulate -> measure -> evaluate -> adjust -> visualize).
- Complete tools reference table (28 tools).
- Supported topologies table with specs and defaults.
- 9 workflow best practices including the critical schematic URL instruction.
- 8 common pitfalls (title line, SPICE mega=`meg` not `M`, ground requirement, etc.).

## local_system_prompt.md

FACTS: Compact executor prompt for local models. 108 lines. Terse style -- "Report numbers, not descriptions." Contains:
- Tools table with required/optional params and return types.
- SPICE netlist syntax reference with value suffix table.
- Shortened workflow (8 steps).
- Output rules: no explanation, no hedging, escalate unsupported requests with `[ESCALATE TO CLOUD]`.
- Templates table with solver specs.

## Architecture Role

Configuration artifacts. Not imported by code. The cloud prompt is referenced in the SKILL.md. The local prompt is designed for use with [prompt_translator.py](prompt_translator.md). See [prompt-translation](../concepts/prompt-translation.md).
