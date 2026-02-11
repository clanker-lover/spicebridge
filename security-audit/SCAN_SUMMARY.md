# Security Audit — Scan Summary

**Date:** 2026-02-11
**Target:** SPICEBridge v0.1.0 (pre-release baseline)
**Source:** `src/spicebridge/` (19 files, ~7,000 LOC)
**Tests:** `tests/` (20 files, ~4,300 LOC)

---

## Scanner Results

### 1. Bandit (src/) — 11 findings

| Severity | Count |
|----------|-------|
| Low      | 11    |
| Medium   | 0     |
| High     | 0     |

**Status:** Completed successfully

Findings by test ID:

| ID | Test Name | Count | Files |
|----|-----------|-------|-------|
| B603 | subprocess_without_shell_equals_true | 2 | `simulator.py` |
| B607 | start_process_with_partial_path | 2 | `simulator.py` |
| B404 | import_subprocess | 1 | `simulator.py` |
| B110 | try_except_pass | 2 | `server.py` |
| B311 | random (non-crypto PRNG) | 1 | `server.py` |
| B405 | import_xml_etree | 1 | `svg_renderer.py` |
| B101 | assert_used | 2 | `template_manager.py` |

### 2. Bandit (tests/) — 810 findings

| Severity | Count |
|----------|-------|
| Low      | 810   |
| Medium   | 0     |
| High     | 0     |

**Status:** Completed successfully

Findings by test ID:

| ID | Test Name | Count |
|----|-----------|-------|
| B101 | assert_used | 801 |
| B314 | blacklist (xml.etree.ElementTree.parse) | 5 |
| B311 | random (non-crypto PRNG) | 3 |
| B405 | import_xml_etree | 1 |

> Note: 801 of 810 findings are `assert` usage in test files, which is expected and non-actionable.

### 3. pip-audit — 3 vulnerabilities in 2 packages

| Package | Version | CVE | Fix Version | Severity |
|---------|---------|-----|-------------|----------|
| cryptography | 46.0.4 | CVE-2026-26007 | 46.0.5 | ECDSA/ECDH subgroup validation bypass (SECT curves only) |
| pip | 24.0 | CVE-2025-8869 | 25.3 | Symlink path traversal in tar extraction |
| pip | 24.0 | CVE-2026-1703 | 26.0 | Path traversal in wheel extraction |

**Status:** Completed successfully

### 4. Semgrep — 2 findings

| Severity | Count |
|----------|-------|
| Error (blocking) | 2 |

**Status:** Completed successfully (308 rules on 32 files)

Findings:

| Rule | File | Line |
|------|------|------|
| python37-compatibility-importlib2 | `template_manager.py` | 5 |
| python37-compatibility-importlib2 | `web_viewer.py` | 11 |

> Both are `importlib.resources` compatibility warnings (Python 3.7+ only). Non-security; informational.

### 5. Dodgy — 0 findings

**Status:** Completed successfully. No hardcoded passwords, secret keys, or suspicious strings detected.

### 6. Safety — 2 vulnerabilities in 1 package

| Package | Version | Vuln ID | Description |
|---------|---------|---------|-------------|
| pip | 24.0 | PVE-2025-75180 | Malicious wheel code execution |
| pip | 24.0 | CVE-2025-8869 | Symlink path traversal in tar extraction |

**Status:** Completed successfully (deprecated `check` command; 109 packages scanned)

### 7. detect-secrets — 0 findings

**Status:** Completed successfully (27 detector plugins). No secrets, API keys, or credentials found in source code.

### 8. Vulture — 23 potentially unused code items

**Status:** Completed successfully

| Category | Count |
|----------|-------|
| Unused functions | 15 |
| Unused methods | 3 |
| Unused variables | 3 |
| Unused attributes | 2 |

> Note: Many "unused" functions in `server.py` are MCP tool handlers registered via decorators, which Vulture cannot detect as used. These are likely false positives.

### 9. Radon (Cyclomatic Complexity) — 15 functions with CC > 10

**Status:** Completed successfully

| Grade | CC Range | Count |
|-------|----------|-------|
| F (very high) | 41+ | 1 |
| E (high) | 31-40 | 1 |
| D (moderate-high) | 21-30 | 3 |
| C (moderate) | 11-20 | 10 |

Worst offenders:

| Function | File | CC | Grade |
|----------|------|----|-------|
| `compose_stages` | `composer.py:308` | 53 | F |
| `prefix_netlist` | `composer.py:120` | 37 | E |
| `_extract_params` | `prompt_translator.py:245` | 30 | D |
| `run_worst_case` | `server.py:1106` | 29 | D |
| `draw_schematic` | `schematic.py:121` | 25 | D |

### 10. Radon (Maintainability Index) — 1 file below grade A

**Status:** Completed successfully

| File | MI Score | Grade |
|------|----------|-------|
| `server.py` | 9.00 | B |

All other 19 files scored grade A (MI > 20). Lowest A-grade files: `solver.py` (23.51), `composer.py` (32.96), `svg_renderer.py` (32.30).

---

## Top 3 Most Concerning Findings

### 1. Subprocess calls with user-influenced content (CRITICAL for internet-exposed service)

**Scanner:** Bandit (B603, B607, B404)
**File:** `src/spicebridge/simulator.py:35, 110`
**Issue:** `subprocess.run()` calls ngspice with user-provided netlist content written to temp files. The netlist content originates from MCP tool parameters (user-influenced). While `shell=False` is used (good), the netlist file content itself could contain ngspice directives that execute arbitrary commands (e.g., `.system` or `.exec` directives in SPICE).
**Risk:** Command injection via SPICE directives in an internet-exposed MCP server.

### 2. Known vulnerabilities in dependencies (cryptography, pip)

**Scanner:** pip-audit, Safety
**Packages:** `cryptography` 46.0.4 (CVE-2026-26007), `pip` 24.0 (CVE-2025-8869, CVE-2026-1703)
**Issue:** The cryptography library has an ECDSA/ECDH subgroup validation bypass (SECT curves). pip has path traversal vulnerabilities in archive extraction.
**Risk:** Dependency-level vulnerabilities; cryptography issue affects SECT curves only. pip issues affect package installation.

### 3. Extreme cyclomatic complexity in `compose_stages` (CC=53)

**Scanner:** Radon
**File:** `src/spicebridge/composer.py:308`
**Issue:** `compose_stages()` has a cyclomatic complexity of 53, which is classified as "untestable" by most standards. `prefix_netlist()` at CC=37 is similarly concerning. High complexity correlates strongly with security bugs due to difficulty in reasoning about all code paths.
**Risk:** Hidden logic bugs and missed edge cases in circuit composition, which processes user input.

---

## Files in This Report

| File | Scanner |
|------|---------|
| `reports/bandit.json` | Bandit (src, JSON) |
| `reports/bandit.txt` | Bandit (src, text) |
| `reports/bandit-tests.txt` | Bandit (tests, text) |
| `reports/pip-audit.json` | pip-audit (JSON) |
| `reports/pip-audit.txt` | pip-audit (text) |
| `reports/semgrep.json` | Semgrep (JSON) |
| `reports/semgrep.txt` | Semgrep (text) |
| `reports/dodgy.txt` | Dodgy |
| `reports/safety.json` | Safety (JSON) |
| `reports/safety.txt` | Safety (text) |
| `reports/detect-secrets.json` | detect-secrets |
| `reports/vulture.txt` | Vulture |
| `reports/radon-complexity.txt` | Radon CC |
| `reports/radon-maintainability.txt` | Radon MI |
