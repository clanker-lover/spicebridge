# SPICEBridge Wiki Index

**Repository:** `~/spicebridge/` -- AI-powered circuit design tool (vibe-CAD for electronics)
**Version:** 1.3.0 | **License:** GPL-3.0-or-later | **Author:** Brandon
**Generated:** 2026-04-05

---

## Summaries

One page per significant source file. Each covers: purpose, public API, key types, dependencies, architecture role.

### Core Server
- [server.py](summaries/server.md) -- MCP server with 28 tools, the main integration point
- [__main__.py](summaries/__main__.md) -- CLI entry point, transport selection, startup
- [__init__.py](summaries/__init__.md) -- Package init with lazy imports

### Simulation Engine
- [simulator.py](summaries/simulator.md) -- ngspice execution with concurrency control
- [parser.py](summaries/parser.md) -- `.raw` file parsing for AC/transient/DC results
- [netlist_utils.py](summaries/netlist_utils.md) -- Netlist preparation (strip/append analysis commands)

### Circuit Management
- [circuit_manager.py](summaries/circuit_manager.md) -- In-memory circuit session state
- [schematic_cache.py](summaries/schematic_cache.md) -- FIFO PNG cache for HTTP serving

### Template & Solver
- [template_manager.py](summaries/template_manager.md) -- Template discovery, loading, parameter substitution
- [solver.py](summaries/solver.md) -- Design equation solvers for 12 topologies
- [standard_values.py](summaries/standard_values.md) -- E-series snapping and engineering notation
- [templates JSON](summaries/templates_json.md) -- 11 built-in circuit template data files

### Visualization
- [schematic.py](summaries/schematic.md) -- Static schematic rendering via schemdraw
- [svg_renderer.py](summaries/svg_renderer.md) -- Interactive SVG with hover effects
- [kicad_export.py](summaries/kicad_export.md) -- KiCad 8 schematic export
- [web_viewer.py](summaries/web_viewer.md) -- Browser-based viewer with WebSocket updates

### Analysis
- [monte_carlo.py](summaries/monte_carlo.md) -- Monte Carlo and worst-case tolerance analysis

### Model Library
- [model_generator.py](summaries/model_generator.md) -- SPICE model generation from datasheet params
- [model_store.py](summaries/model_store.md) -- Persistent model storage (~/.spicebridge/models/)

### Security & Auth
- [sanitize.py](summaries/sanitize.md) -- Input validation and injection prevention
- [auth.py](summaries/auth.md) -- API key authentication middleware

### Infrastructure
- [constants.py](summaries/constants.md) -- Shared constants (node counts, regex patterns)
- [metrics.py](summaries/metrics.md) -- Persistent metrics collector with health endpoint
- [setup_wizard.py](summaries/setup_wizard.md) -- Cloudflare tunnel deployment wizard

### AI Integration
- [prompt_translator.py](summaries/prompt_translator.md) -- English-to-structured-JSON prompt preprocessing
- [prompt files](summaries/prompt_files.md) -- System prompts for cloud and local AI models
- [SKILL.md](summaries/skill_md.md) -- Claude Code skill definition

### Package Config
- [pyproject.toml](summaries/pyproject.md) -- Build config, dependencies, entry points

---

## Concepts

Cross-cutting articles synthesizing patterns across multiple source files.

- [MCP Tool Architecture](concepts/mcp-tool-architecture.md) -- FastMCP integration, tool annotations, transport modes
- [Simulation Pipeline](concepts/simulation-pipeline.md) -- End-to-end flow from netlist to parsed results
- [Template System](concepts/template-system.md) -- Templates + solvers + standard values stack
- [Security Model](concepts/security-model.md) -- Input validation, auth, path safety, error sanitization
- [Visualization Pipeline](concepts/visualization-pipeline.md) -- Four output formats sharing common layout
- [Circuit Lifecycle](concepts/circuit-lifecycle.md) -- Creation, simulation, modification, deletion
- [Tolerance Analysis](concepts/tolerance-analysis.md) -- Monte Carlo and worst-case methods
- [Multi-Stage Composition](concepts/multi-stage-composition.md) -- Connecting circuits with port wiring
- [Observability](concepts/observability.md) -- Metrics, health endpoint, rate limiting
- [Model Library](concepts/model-library.md) -- Generating and persisting SPICE models
- [Cloud Deployment](concepts/cloud-deployment.md) -- Cloudflare tunnels, env vars, remote mode
- [Prompt Translation](concepts/prompt-translation.md) -- Three-tier AI integration strategy

---

## Entities

People, tools, libraries, and services referenced in the codebase.

- [Dependencies](entities/dependencies.md) -- All runtime and dev dependencies with versions
- [ngspice](entities/ngspice.md) -- The SPICE simulation engine
- [Cloudflare](entities/cloudflare.md) -- Tunnel service for cloud deployment
- [KiCad](entities/kicad.md) -- EDA software for schematic/PCB export
- [MCP](entities/mcp.md) -- Model Context Protocol framework
- [Brandon](entities/brandon.md) -- Project author

---

## Meta

- [Ingest Log](log.md) -- Record of source files processed and wiki generation details
