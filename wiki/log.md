# Ingest Log

## 2026-04-05 -- Initial Wiki Generation

**Source repository:** `~/spicebridge/`
**Commit/state:** Working tree at time of ingest (no git metadata read)

### Files Ingested

**Python source** (26 files):
- `src/spicebridge/__init__.py`, `__main__.py`, `auth.py`, `circuit_manager.py`, `composer.py`, `constants.py`, `kicad_export.py`, `metrics.py`, `model_generator.py`, `model_store.py`, `monte_carlo.py`, `netlist_utils.py`, `parser.py`, `prompt_translator.py`, `sanitize.py`, `schematic.py`, `schematic_cache.py`, `server.py`, `setup_wizard.py`, `simulator.py`, `solver.py`, `standard_values.py`, `svg_renderer.py`, `template_manager.py`, `web_viewer.py`
- `src/spicebridge/static/__init__.py` (empty, not summarized)

**Configuration** (1 file): `pyproject.toml`

**Documentation** (6 files): `README.md`, `PRIVACY.md`, `TERMS.md`, `docs/cloud-setup.md`, `prompts/cloud_system_prompt.md`, `prompts/local_system_prompt.md`

**Skill definition** (1 file): `skills/spicebridge/SKILL.md`

**Template data** (11 JSON files): `src/spicebridge/templates/*.json` (2 read in full, 9 covered by template summary)

**Test files** (32 files): Listed but not individually summarized. Tests follow `test_*.py` naming convention in `tests/` directory.

### Wiki Pages Generated

- **Summaries**: 22 pages covering all significant source files and documentation
- **Concepts**: 10 cross-cutting concept articles
- **Entities**: 6 entity pages (dependencies, ngspice, cloudflare, kicad, mcp, brandon)
- **Index**: 1 master catalog
- **Log**: 1 ingest log (this file)

### Notes

- `server.py` was read in 5 chunks due to size (1050+ lines).
- `kicad_export.py` was read in 2 chunks due to size (contains embedded KiCad lib symbol templates).
- Test files were not individually summarized as they follow standard pytest patterns and the public API is well-covered by source summaries.
- `src/spicebridge/static/` contains `__init__.py` (empty), `logo.svg`, and `viewer.html` -- the HTML/SVG assets were not read but their roles are documented in relevant summaries.
