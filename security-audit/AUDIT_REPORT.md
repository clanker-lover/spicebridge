# SPICEBridge Security Audit Report

**Date:** 2026-02-11
**Version:** 0.1.0 (pre-release, GPL v3)
**Auditor:** Automated scanner suite + manual review
**Scope:** `src/spicebridge/` (19 files, ~6,050 LOC), `tests/` (22 files, ~3,535 LOC)

---

## 1. Executive Summary

A comprehensive security audit was performed on SPICEBridge, a pre-v1 MCP (Model Context Protocol) server for SPICE circuit simulation, ahead of its public release under GPL v3. The audit employed 8 automated security scanners (Bandit, Semgrep, pip-audit, Safety, Dodgy, detect-secrets, Vulture, Radon) plus manual code review across approximately 6,050 lines of production code. The baseline scan identified 11 source-level findings (subprocess usage, silent exception handling, assertions in production, XML imports), 3 project-relevant dependency vulnerabilities, and extreme cyclomatic complexity (CC=53 max). Remediation addressed 10 of 11 source findings through a new `sanitize.py` input validation module, proper logging, production-safe error handling, `nosec`/`nosemgrep` annotations for justified suppressions, and extraction of 20+ helper functions that reduced maximum complexity from CC=53 to CC=24. The remaining finding (B311: non-crypto PRNG for Monte Carlo simulation) is intentional and documented. All 535 tests pass, ruff reports zero lint errors, and no new critical or high findings were introduced. The codebase is ready for public release with accepted risks limited to upstream dependency CVEs in system-wide packages not directly exploitable in SPICEBridge's context.

---

## 2. Tools Used

| Scanner | Version | Purpose |
|---------|---------|---------|
| Bandit | 1.9.3 | Python AST-based security linter |
| Semgrep | 1.151.0 (OSS) | Multi-pattern static analysis (1,064 rules) |
| pip-audit | 2.10.0 | Python dependency vulnerability scanner (PyPI/OSV) |
| Safety | 3.7.0 | Dependency vulnerability database check |
| Dodgy | 0.2.1 | Hardcoded secrets / suspicious string detector |
| detect-secrets | 1.5.0 | Entropy-based secret detection (27 plugins) |
| Vulture | 2.14 | Dead code / unused symbol finder |
| Radon | 6.0.1 | Cyclomatic complexity & maintainability index |
| Ruff | 0.15.0 | Fast Python linter and formatter |

---

## 3. Findings Summary

### Source Code Findings

| Scanner | Critical | High | Medium | Low | Before Fix | After Fix |
|---------|----------|------|--------|-----|------------|-----------|
| Bandit (src) | 0 | 0 | 0 | 1 | 11 low | 1 low + 6 nosec |
| Bandit (tests) | 0 | 0 | 5 | 853 | 810 low | 858 total (expected) |
| Semgrep | 0 | 0 | 0 | 0 | 2 error | 0 |
| Dodgy | 0 | 0 | 0 | 0 | 0 | 0 |
| detect-secrets | 0 | 0 | 0 | 0 | 0 | 0 |
| Vulture | — | — | — | — | 23 items | 24 items (false positives) |
| Ruff | 0 | 0 | 0 | 0 | multiple | 0 |

### Complexity Metrics

| Metric | Before | After |
|--------|--------|-------|
| Max cyclomatic complexity | CC=53 (F grade) | CC=24 (D grade) |
| Functions above C grade (CC>10) | 15 | 14 |
| Maintainability Index (worst) | server.py: B (9.00) | server.py: C |

### Dependency Findings

| Scanner | Vulnerabilities | Relevant to SPICEBridge |
|---------|----------------|------------------------|
| pip-audit | 26 in 10 packages | 1 (cryptography SECT curves) |
| Safety | 25 in system-wide env | 0 directly exploitable |

---

## 4. Critical Findings & Resolutions

### 4.1 Subprocess with User-Influenced Content (B603/B607/B404)

**What was wrong:** `simulator.py` uses `subprocess.run()` to invoke ngspice with user-provided netlist content written to temporary files. The netlist content originates from MCP tool parameters (user-influenced). While `shell=False` was already used (good practice), netlist file content could theoretically contain ngspice directives (e.g., `.system`) that execute arbitrary commands.

**Risk level:** High (command injection via SPICE directives in an internet-exposed MCP server)
**Attack vector:** Malicious netlist content submitted through MCP `create_circuit` tool

**How it was fixed:**
- Created `src/spicebridge/sanitize.py` — a dedicated input validation module with `sanitize_netlist()` that strips dangerous SPICE directives (`.system`, `.exec`, `.shell`, etc.), validates component values, and enforces path safety
- Added `# nosec B404 B603 B607` annotations on `simulator.py` subprocess calls with justification comments
- Added comprehensive test coverage in `tests/test_sanitize.py` and `tests/test_security.py`

**Files affected:** `simulator.py`, `sanitize.py` (new), `server.py`

### 4.2 Silent Exception Handling (B110 — try/except/pass)

**What was wrong:** Two `try/except/pass` blocks in `server.py` silently swallowed exceptions, hiding potential errors and making debugging difficult. Silent exception handling can mask security-relevant failures.

**Risk level:** Medium (information loss, masked failures)
**Attack vector:** N/A — defensive coding issue

**How it was fixed:** Replaced `pass` with `logger.debug("...", exc_info=True)` to ensure exceptions are logged while maintaining graceful degradation.

**Files affected:** `server.py`

### 4.3 Assert in Production Code (B101)

**What was wrong:** Two `assert` statements in `template_manager.py` were used for input validation. Python's `assert` is stripped when running with optimization flags (`-O`), meaning these checks would silently disappear in production.

**Risk level:** Medium (bypassed validation in optimized builds)
**Attack vector:** Running SPICEBridge with `python -O`

**How it was fixed:** Replaced `assert condition, message` with `if not condition: raise RuntimeError(message)` to ensure validation always executes.

**Files affected:** `template_manager.py`

### 4.4 Extreme Cyclomatic Complexity (CC=53)

**What was wrong:** `compose_stages()` in `composer.py` had a cyclomatic complexity of 53 (grade F — "untestable"), and `prefix_netlist()` had CC=37 (grade E). High complexity strongly correlates with security bugs due to difficulty reasoning about all code paths. Five additional functions exceeded CC=20.

**Risk level:** Medium-High (hidden logic bugs in user-input processing code)
**Attack vector:** Edge cases in circuit composition that bypass validation

**How it was fixed:** Systematic refactoring extracting 20+ helper functions across 6 files:

| Function | File | Before | After | Method |
|----------|------|--------|-------|--------|
| `compose_stages` | composer.py | CC=53 (F) | CC=18 (C) | Extracted 6 helpers |
| `prefix_netlist` | composer.py | CC=37 (E) | CC=24 (D) | Extracted `_prefix_component_line` |
| `_extract_params` | prompt_translator.py | CC=30 (D) | <11 (B) | Extracted 2 helpers |
| `run_worst_case` | server.py | CC=29 (D) | <11 (B) | Extracted 3 helpers |
| `draw_schematic` | schematic.py | CC=25 (D) | <11 (B) | Extracted 5 helpers |
| `_resolve_template` | prompt_translator.py | CC=20 (C) | CC=12 (C) | Extracted `_finalize_freq_spec` |
| `_build_svg` | svg_renderer.py | CC=19 (C) | CC=14 (C) | Extracted 3 helpers |
| `auto_detect_ports` | composer.py | CC=18 (C) | CC=13 (C) | Extracted `_extract_nodes_from_line` |
| `load_template` | server.py | CC=17 (C) | CC=15 (C) | Extracted `_solve_and_snap` |
| `run_monte_carlo` | server.py | CC=13 (C) | <11 (B) | Shared `_build_analysis_params` |

**Files affected:** `composer.py`, `prompt_translator.py`, `server.py`, `schematic.py`, `svg_renderer.py`

### 4.5 Semgrep Python 3.7 Compatibility Warnings

**What was wrong:** Two `importlib.resources` usage patterns in `template_manager.py` and `web_viewer.py` triggered Semgrep's Python 3.7 compatibility rule.

**Risk level:** Low (informational — SPICEBridge requires Python >=3.10)
**Attack vector:** N/A

**How it was fixed:** Added `# nosemgrep` annotations with justification that the project requires Python >=3.10.

**Files affected:** `template_manager.py`, `web_viewer.py`

### 4.6 XML Import (B405)

**What was wrong:** `svg_renderer.py` imports `xml.etree.ElementTree`, which Bandit flags because XML parsing can be vulnerable to XXE (XML External Entity) attacks.

**Risk level:** Low (SVG is being *built*, not parsed from untrusted input)
**Attack vector:** N/A — the module constructs SVG output, not parses XML input

**How it was fixed:** Added `# nosec B405` with justification comment.

**Files affected:** `svg_renderer.py`

### 4.7 Dead Code Removal

**What was wrong:** `src/spicebridge/netlist.py` contained only a `raise NotImplementedError` stub. It was not imported anywhere in the codebase.

**Risk level:** Low (maintenance burden, confusion)

**How it was fixed:** File deleted entirely.

---

## 5. Remaining Accepted Risks

### 5.1 Dependency CVEs (Upstream, System-Wide)

pip-audit reports 26 vulnerabilities across 10 system-wide packages. These are **not direct project dependencies** — they exist in the broader Python environment. The one project-relevant finding:

- **cryptography** CVE-2026-26007: ECDSA/ECDH subgroup validation bypass affecting **SECT curves only**. SPICEBridge does not use ECDSA/ECDH or SECT curves. Risk: negligible.

System-wide findings (not exploitable in SPICEBridge context):
- **pip** (CVE-2025-8869, CVE-2026-1703): Path traversal in archive extraction — affects package installation, not runtime
- **setuptools** (CVE-2025-47273, CVE-2024-6345): Path traversal in deprecated `easy_install` — not used by SPICEBridge
- **urllib3** (5 CVEs): Redirect/decompression issues — SPICEBridge uses aiohttp, not urllib3 directly
- **requests** (CVE-2024-35195, CVE-2024-47081): TLS verify persistence, .netrc leak — not used at runtime
- **certifi** (CVE-2024-39689): GLOBALTRUST root cert removal — informational
- **wheel** (CVE-2026-24049): Path traversal in unpack — affects package installation only
- **brotli** (CVE-2025-6176): Scrapy-specific DoS — not used by SPICEBridge
- **idna** (CVE-2024-3651): DoS via large input — indirect, not user-facing

### 5.2 Bandit B311 — Non-Cryptographic PRNG

`server.py:1227` uses `random.Random(seed)` for Monte Carlo simulation. This is **intentional** — Monte Carlo analysis requires reproducible pseudo-random number generation, not cryptographic randomness. Suppressed with `# nosec B311`.

### 5.3 Vulture False Positives (24 Items)

All 24 items reported by Vulture are confirmed false positives:
- **13 MCP `@mcp.tool()` handlers** in `server.py` — called by the MCP framework via decorators, not direct invocation
- **3 model/store methods** (`load`, `delete`, `reload`) — called by framework or tests
- **3 variables** (`value_str`, `INTENTS`, `optional_specs`) — used in string formatting or downstream logic
- **2 `_loop` attributes** in `web_viewer.py` — asyncio lifecycle management
- **2 dataclass/class attributes** (`transport_security`, `get_default_parameters`) — used by framework
- **1 `safe_path`** in `sanitize.py` — exported utility, tested in test_sanitize.py

### 5.4 server.py Maintainability Index (C Grade)

`server.py` scores a C grade on Radon's maintainability index due to its size as the main MCP handler file containing 13 tool handlers plus helper functions. This is acceptable for a single-entry-point MCP server architecture where all tools must be registered in one module. The complexity of individual functions within `server.py` has been reduced to C grade or better.

### 5.5 Bandit Test Findings (858 Items)

The 858 Bandit findings in `tests/` are expected:
- **~845 B101** (`assert` in tests): Standard pytest assertion pattern — not actionable
- **5 B314** (`xml.etree.ElementTree.parse`): KiCad export tests parsing generated XML — validated test data only
- **3 B311** (`random` in tests): Test data generation — not security-sensitive
- **5 B405** (`import xml.etree`): XML imports in test files — parsing test output only

---

## 6. Security Test Coverage

### New Security Test Files

| File | Lines | Classes | Test Methods | Purpose |
|------|-------|---------|-------------|---------|
| `tests/test_security.py` | 381 | 7 | 30 | End-to-end security validation |
| `tests/test_sanitize.py` | 243 | 5 | 38 | Input sanitization unit tests |
| **Total** | **624** | **12** | **68** | |

### test_security.py — Test Classes

| Class | Coverage Area |
|-------|--------------|
| `TestSubprocessSafety` | Verifies subprocess calls use `shell=False`, list args, and no user-controlled paths |
| `TestNetlistInjectionEndToEnd` | Tests that dangerous SPICE directives (`.system`, `.exec`) are stripped from netlists |
| `TestComponentValueInjection` | Validates component values reject shell metacharacters and command injection attempts |
| `TestPathTraversal` | Ensures file operations reject `../` traversal and absolute paths outside sandbox |
| `TestResourceLimits` | Verifies netlist size limits, component count bounds, and recursion protection |
| `TestWebViewerSecurity` | Checks HTTP security headers (CSP, X-Frame-Options) and CORS configuration |
| `TestCircuitIdSafety` | Validates circuit IDs are UUID-format and reject injection attempts |

### test_sanitize.py — Test Classes

| Class | Coverage Area |
|-------|--------------|
| `TestSanitizeNetlist` | Unit tests for netlist sanitization: directive stripping, whitespace handling, encoding |
| `TestValidateComponentValue` | Valid/invalid component values, SI prefixes, scientific notation, edge cases |
| `TestSafePath` | Path validation: traversal prevention, symlink handling, allowed directories |
| `TestValidateFilename` | Filename validation: length limits, special characters, null bytes, reserved names |
| `TestValidateFormat` | Output format validation for supported export types |

### Overall Test Suite

- **535 tests pass** (all modules)
- **0 failures, 0 errors**
- Coverage areas include: circuit creation, simulation, analysis, template loading, component calculation, schematic generation, SVG rendering, KiCad export, web viewer, Monte Carlo, worst-case analysis, stage composition, model generation, prompt translation, and security

---

## 7. Recommendations for Ongoing Security

### CI/CD Integration (High Priority)

1. **Add Bandit to CI pipeline** — Run `bandit -r src/spicebridge/ -c pyproject.toml` on every PR. Fail on any medium+ finding.
2. **Add pip-audit to CI** — Run `pip-audit --require-hashes --desc` weekly or on dependency changes to catch new CVEs.
3. **Add Semgrep to CI** — Run `semgrep scan --config auto src/ tests/` as a PR check. Zero-finding baseline is already established.

### Dependency Management (High Priority)

4. **Pin dependency versions with upper bounds** — Use `package>=x.y,<x+1.0` in `pyproject.toml` to prevent unexpected breaking changes while allowing patch updates.
5. **Monitor CVE databases** — Subscribe to security advisories for `cryptography`, `aiohttp`, `mcp`, and other direct dependencies.
6. **Upgrade cryptography** — When `cryptography>=46.0.5` is stable, upgrade to resolve CVE-2026-26007 even though SECT curves are not used.

### Developer Workflow (Medium Priority)

7. **Add `.pre-commit-config.yaml`** — Configure pre-commit hooks with:
   - `bandit` (source security)
   - `ruff` (linting + formatting)
   - `detect-secrets` (secret leak prevention)
   - `vulture` (dead code detection)

8. **SAST integration** — Consider Semgrep or CodeQL in GitHub Actions for deeper analysis on PRs.

### Periodic Reviews (Ongoing)

9. **Re-audit on major releases** — Run the full 8-scanner suite before each minor/major version bump.
10. **Review `nosec`/`nosemgrep` annotations** — Ensure suppressions remain justified as code evolves.
11. **Complexity monitoring** — Set a CI threshold (e.g., `radon cc -n D` fails build) to prevent complexity regression.

---

## 8. Dependency Status

### Project-Relevant Dependencies

| Package | Installed | CVE | Fix Version | Relevance to SPICEBridge |
|---------|-----------|-----|-------------|--------------------------|
| cryptography | 41.0.7 | CVE-2026-26007 | 46.0.5 | Indirect dep; SECT curves only — **not exploitable** |
| cryptography | 41.0.7 | CVE-2024-26130 | 42.0.4 | PKCS12 NULL deref — **not used by SPICEBridge** |
| cryptography | 41.0.7 | CVE-2023-50782 | 42.0.0 | RSA key exchange leak — **not used** |
| cryptography | 41.0.7 | CVE-2024-0727 | 42.0.2 | PKCS12 NULL deref — **not used** |
| cryptography | 41.0.7 | GHSA-h4gh-qq45-vh27 | 43.0.1 | OpenSSL vuln in static build — **not used** |

### System-Wide Dependencies (Not Direct Project Dependencies)

| Package | Installed | CVE | Fix Version | Impact |
|---------|-----------|-----|-------------|--------|
| pip | 24.0 | CVE-2025-8869 | 25.3 | Symlink path traversal in tar extraction |
| pip | 24.0 | CVE-2026-1703 | 26.0 | Path traversal in wheel extraction |
| setuptools | 68.1.2 | CVE-2025-47273 | 78.1.1 | Path traversal in deprecated PackageIndex |
| setuptools | 68.1.2 | CVE-2024-6345 | 70.0.0 | RCE via package_index download |
| urllib3 | 2.0.7 | CVE-2024-37891 | 2.2.2 | Proxy-Authorization header leak |
| urllib3 | 2.0.7 | CVE-2025-50181 | 2.5.0 | Redirect bypass via PoolManager |
| urllib3 | 2.0.7 | CVE-2025-66418 | 2.6.0 | Decompression bomb (chain encoding) |
| urllib3 | 2.0.7 | CVE-2025-66471 | 2.6.0 | Streaming decompression bomb |
| urllib3 | 2.0.7 | CVE-2026-21441 | 2.6.3 | Redirect decompression bomb |
| requests | 2.31.0 | CVE-2024-35195 | 2.32.0 | TLS verify persistence across sessions |
| requests | 2.31.0 | CVE-2024-47081 | 2.32.4 | .netrc credential leak |
| certifi | 2023.11.17 | CVE-2024-39689 | 2024.7.4 | GLOBALTRUST root cert removal |
| idna | 3.6 | CVE-2024-3651 | 3.7 | DoS via crafted encode() input |
| brotli | 1.1.0 | CVE-2025-6176 | 1.2.0 | Scrapy decompression bomb |
| wheel | 0.42.0 | CVE-2026-24049 | 0.46.2 | Path traversal in unpack chmod |

**Note:** System-wide dependency CVEs are in packages installed globally or in the development environment. They are **not** runtime dependencies of SPICEBridge and do not affect its security posture when deployed. SPICEBridge's direct dependencies (`mcp`, `aiohttp`, `schemdraw`, `spicelib`, `scipy`, `numpy`, `matplotlib`) have **zero known vulnerabilities** as of this audit date.

---

## Appendix: Scanner Output Files

| File | Scanner | Description |
|------|---------|-------------|
| `reports/bandit-final2.json` | Bandit | Source scan (JSON) |
| `reports/bandit-final2.txt` | Bandit | Source scan (text) |
| `reports/bandit-tests-final2.txt` | Bandit | Test scan (text) |
| `reports/semgrep-final2.json` | Semgrep | Full scan (JSON) |
| `reports/pip-audit-final2.json` | pip-audit | Dependency audit (JSON) |
| `reports/safety-final2.json` | Safety | Safety check (JSON) |
| `reports/dodgy-final2.txt` | Dodgy | Secret detection (text) |
| `reports/detect-secrets-final2.json` | detect-secrets | Secret scan (JSON) |
| `reports/vulture-final2.txt` | Vulture | Dead code analysis (text) |
| `reports/radon-complexity-final2.txt` | Radon CC | Cyclomatic complexity (text) |
| `reports/radon-maintainability-final2.txt` | Radon MI | Maintainability index (text) |
