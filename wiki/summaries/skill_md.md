# SKILL.md

**Source:** `skills/spicebridge/SKILL.md`

## Purpose

Claude Code skill definition for SPICEBridge. Defines trigger keywords, recommended workflow, template selection guidance, design gotchas, result interpretation, and schematic URL handling rules.

## Key Facts

- **Trigger keywords**: circuit design, filters (low-pass, high-pass, bandpass, notch), amplifiers, SPICE simulation, AC/DC/transient analysis, schematics, component selection, frequency response, Monte Carlo, KiCad export.
- **`user-invocable: false`**: Auto-triggered by keyword detection, not manually invoked.

## Workflow Prescription

1. Identify topology -> match to template.
2. Call `auto_design` with specs (one-shot design+simulate+verify).
3. Draw schematic, share URL.
4. Review comparison results, adjust if needed.
5. Offer Monte Carlo analysis for production designs.

## Design Guidance

FACTS: Contains detailed guidance on E24 rounding effects, first-order vs second-order filter selection, passive vs active filter tradeoffs, impedance considerations (1k-100k for resistors, 100pF-10uF for capacitors). (Source: `skills/spicebridge/SKILL.md`)

## Architecture Role

AI integration artifact. Not imported by code. Configures how Claude Code interacts with SPICEBridge tools.
