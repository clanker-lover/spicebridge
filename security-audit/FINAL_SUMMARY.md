# Security Audit — Final Summary

Pre-v1 security audit for SPICEBridge GPL v3 public release.
Three-part remediation: Parts 1-2 (high severity), Part 3 (medium/low + dead code + complexity).

## Scanner Results: Before vs After

| Scanner | Baseline | Final | Delta |
|---------|----------|-------|-------|
| **Bandit** (src) | 11 low-severity issues | 1 low (6 nosec suppressions) | -10 issues |
| **Bandit** (tests) | 0 issues | 0 issues | no change |
| **Semgrep** | 2 findings (importlib compat) | 0 findings | -2 findings |
| **Dodgy** | 0 warnings | 0 warnings | no change |
| **Detect-secrets** | 0 secrets | 0 secrets | no change |
| **Vulture** | 23 items (all false positives) | 23 items (all false positives) | no change |
| **Radon CC** (max) | F (53) — `compose_stages` | D (24) — `prefix_netlist` | max CC 53 -> 24 |
| **Radon CC** (avg >C) | 14 functions above C | 14 functions above C | redistribution |
| **pip-audit** | 26 known vulns (deps) | 26 known vulns (deps) | no change (upstream) |
| **Safety** | dep advisories | dep advisories | no change (upstream) |
| **Ruff** (modified files) | multiple E402/I001 | 0 errors | clean |

## Complexity Reduction Detail

| Function | File | Before | After | Method |
|----------|------|--------|-------|--------|
| `compose_stages` | composer.py | CC=53 (F) | CC=18 (C) | Extracted 6 helpers |
| `prefix_netlist` | composer.py | CC=37 (E) | CC=24 (D) | Extracted `_prefix_component_line` |
| `_extract_params` | prompt_translator.py | CC=30 (D) | <11 (B) | Extracted `_collect_numeric_values`, `_map_values_to_specs` |
| `run_worst_case` | server.py | CC=29 (D) | <11 (B) | Extracted `_build_analysis_params`, `_run_sensitivity_sweep`, `_run_corner_analysis` |
| `draw_schematic` | schematic.py | CC=25 (D) | <11 (B) | Extracted 5 helpers |
| `_resolve_template` | prompt_translator.py | CC=20 (C) | CC=12 (C) | Extracted `_finalize_freq_spec` |
| `_build_svg` | svg_renderer.py | CC=19 (C) | CC=14 (C) | Extracted `_build_svg_defs`, `_render_components`, `_render_wires` |
| `auto_detect_ports` | composer.py | CC=18 (C) | CC=13 (C) | Extracted `_extract_nodes_from_line` |
| `load_template` | server.py | CC=17 (C) | CC=15 (C) | Extracted `_solve_and_snap` |
| `run_monte_carlo` | server.py | CC=13 (C) | <11 (B) | Shared `_build_analysis_params` |

## Bandit Finding Disposition

| ID | Location | Disposition |
|----|----------|-------------|
| B110 | server.py (x2) | Fixed: `pass` -> `logger.debug(..., exc_info=True)` |
| B101 | template_manager.py (x2) | Fixed: `assert` -> `if/raise RuntimeError` |
| B405 | svg_renderer.py | Suppressed: `# nosec B405` — builds SVG, not parsing untrusted XML |
| B404 | simulator.py | Suppressed: `# nosec B404` — subprocess with list args, no shell |
| B603/B607 | simulator.py (x2) | Suppressed: `# nosec B603 B607` — list args, trusted binary |
| B311 | server.py | Suppressed: `# nosec B311` — non-crypto PRNG for Monte Carlo |

## Semgrep Finding Disposition

| Rule | Location | Disposition |
|------|----------|-------------|
| python37-compatibility-importlib2 | template_manager.py | Suppressed: `# nosemgrep` — requires Python >=3.10 |
| python37-compatibility-importlib2 | web_viewer.py | Suppressed: `# nosemgrep` — requires Python >=3.10 |

## Dead Code Removed

| File | Reason |
|------|--------|
| `src/spicebridge/netlist.py` | Stub with only `raise NotImplementedError`. Not imported anywhere. |

## Vulture False Positives (kept)

All 23 vulture items are confirmed false positives: MCP `@mcp.tool()` handlers (called by framework), tested methods, dataclass fields, and lifecycle management attributes. No action needed.

## Test Results

- **489 tests passed** (all modules)
- 8 pre-existing fixture errors in `test_web_viewer.py` (missing `aiohttp_client`) — unrelated

## Files Modified

| File | Changes |
|------|---------|
| `src/spicebridge/server.py` | Added logging, replaced pass->debug, nosec B311, extracted helpers, fixed import order |
| `src/spicebridge/template_manager.py` | Replaced assert->if/raise, nosemgrep comment |
| `src/spicebridge/simulator.py` | nosec B404/B603/B607 comments |
| `src/spicebridge/svg_renderer.py` | nosec B405, extracted `_build_svg` helpers |
| `src/spicebridge/web_viewer.py` | nosemgrep comment |
| `src/spicebridge/composer.py` | Extracted helpers for `compose_stages`, `prefix_netlist`, `auto_detect_ports` |
| `src/spicebridge/prompt_translator.py` | Extracted helpers for `_extract_params`, `_resolve_template` |
| `src/spicebridge/schematic.py` | Extracted helpers for `draw_schematic` |
| `src/spicebridge/netlist.py` | **DELETED** (dead stub) |
