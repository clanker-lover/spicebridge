# SPICEBridge — Anthropic Connectors Directory Submission Guide

This guide contains everything you need to fill out the Anthropic MCP Directory Server Review Form for SPICEBridge. The form is at:

https://docs.google.com/forms/d/e/1FAIpQLSeafJF2NDI7oYx1r8o0ycivCSVLNq92Mpc1FPxMKSw1CzDkqA/viewform

## What is SPICEBridge?

SPICEBridge is an MCP server that gives AI models direct access to SPICE circuit simulation via ngspice. Users describe circuits in plain English and the AI handles netlist generation, simulation, measurement, and spec verification. It's an open-source circuit design tool — the only one of its kind in the MCP ecosystem.

- **GitHub**: https://github.com/clanker-lover/spicebridge
- **PyPI**: https://pypi.org/project/spicebridge/
- **License**: GPL-3.0-or-later
- **Public server URL**: https://spicebridge.clanker-lover.work/mcp
- **Transport**: Streamable HTTP
- **Authentication**: None (authless, open access)

## Key facts for the form

- **28 tools** covering the full circuit design workflow
- **11 built-in templates** with automatic component value calculation (E24 standard series)
- All tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, `title`) are implemented on every tool
- The server is production-ready, published on PyPI, and has 771 passing tests
- No user data is collected — circuits are stored in-memory per session and discarded
- No authentication required — no OAuth, no API keys
- HTTPS via Cloudflare tunnel with valid TLS certificate
- The server binds to localhost; Cloudflare tunnel handles external access

## 3+ Example prompts (required)

The form requires at least 3 working examples with realistic user prompts and expected outcomes. Here are strong examples that showcase the breadth of the tool:

### Example 1: Design a filter from specs
**User prompt**: "Design a 1kHz low-pass filter and verify it meets spec"

**What happens**: The AI calls `load_template("rc_lowpass_1st", specs={"f_cutoff_hz": 1000})` which auto-calculates R and C values snapped to E24 standard components, then runs AC analysis, measures the -3dB bandwidth, compares against the 1kHz target, and draws a schematic. The user gets component values, a frequency response, a pass/fail spec check, and a viewable schematic — all from one sentence.

### Example 2: Analyze an existing circuit
**User prompt**: "I have this circuit: [pastes SPICE netlist]. What's the bandwidth and DC operating point?"

**What happens**: The AI calls `create_circuit` with the user's netlist, runs `run_dc_op` to check bias points, then `run_ac_analysis` for the frequency response, and uses `measure_bandwidth` and `measure_dc` to extract the key numbers. It interprets the results in engineering context (e.g., "the op-amp output is at 4.8V, close to the 5V rail — it may be saturating").

### Example 3: Multi-stage design with tolerance analysis
**User prompt**: "Build a second-order Butterworth low-pass at 5kHz, then check how 5% component tolerances affect the cutoff frequency"

**What happens**: The AI calls `load_template("sallen_key_lowpass_2nd", specs={"f_cutoff_hz": 5000, "Q": 0.707})`, validates the netlist, runs AC analysis to confirm the design, then runs `run_monte_carlo` with 100 iterations at 5% tolerance to show the statistical spread of the cutoff frequency. The user sees the nominal result plus min/max/mean/std dev across tolerance variations.

### Example 4: Custom circuit from scratch
**User prompt**: "Create a voltage divider that gives me 3.3V from a 5V supply"

**What happens**: The AI calls `load_template("voltage_divider", specs={"output_voltage": 3.3, "input_voltage": 5})`, which calculates and snaps resistor values to E24 standard. It then runs DC operating point analysis and measures the output node voltage to confirm it's 3.3V. Draws the schematic for the user.

## Privacy policy

SPICEBridge needs a privacy policy link for the submission. Here's the situation:

- The server collects NO user data
- Circuits are stored in-memory per session and discarded when the server restarts
- No analytics, no logging of user prompts, no cookies, no accounts
- The server is open-source — anyone can verify this

**Action needed**: Create a simple privacy policy page (could be a GitHub wiki page, a section in the README, or a standalone page) that states the above. The form requires a URL to it. A minimal one would say something like:

> SPICEBridge does not collect, store, or transmit any personal data. Circuit netlists submitted for simulation are held in server memory for the duration of the session and are not persisted to disk or shared with third parties. No analytics, cookies, or user tracking of any kind is employed. The server source code is publicly available at https://github.com/clanker-lover/spicebridge for independent verification.

## Support contact

The form requires verified contact information and support channels. Options:
- GitHub Issues: https://github.com/clanker-lover/spicebridge/issues
- Email: bwlightnerp@gmail.com (the Google account already associated with the form)

## Documentation link

The README serves as the primary documentation:
https://github.com/clanker-lover/spicebridge/blob/main/README.md

Additional docs:
- Cloud setup guide: https://github.com/clanker-lover/spicebridge/blob/main/docs/cloud-setup.md

## Test credentials

Not applicable — SPICEBridge requires no authentication. The form should note that anyone can connect to `https://spicebridge.clanker-lover.work/mcp` without credentials.

## Things to watch out for

1. **The form is long** — Anthropic warns to review the submission docs first
2. **Production-ready status required** — SPICEBridge is published on PyPI and production-deployed, so this is satisfied
3. **Tool descriptions must precisely match functionality** — All 28 tool docstrings accurately describe what each tool does
4. **No financial transactions** — Not applicable
5. **No ad serving** — Not applicable
6. **No content generation limitations concern** — SPICEBridge generates circuit schematics (diagrams/visual aids for engineering), which is explicitly permitted under the "design-focused software creating visual aids" exception
7. **SSE transport is being deprecated** — SPICEBridge supports Streamable HTTP, which is the required transport

## Relevant policy links for reference

- Remote MCP Server Submission Guide: https://support.claude.com/en/articles/12922490-remote-mcp-server-submission-guide
- Software Directory Policy: https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy
- Software Directory Terms: https://support.claude.com/en/articles/13145338-anthropic-software-directory-terms
- Connectors Directory FAQ: https://support.claude.com/en/articles/11596036-anthropic-connectors-directory-faq
