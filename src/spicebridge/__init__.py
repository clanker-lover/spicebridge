"""SPICEBridge — AI-powered circuit design through simulation."""

__version__ = "1.0.0"

_LAZY_IMPORTS = {
    "run_simulation": "spicebridge.simulator",
    "parse_results": "spicebridge.parser",
    "read_ac_at_frequency": "spicebridge.parser",
    "read_ac_bandwidth": "spicebridge.parser",
    "parse_netlist": "spicebridge.schematic",
    "draw_schematic": "spicebridge.schematic",
    "export_kicad_schematic": "spicebridge.kicad_export",
    "TemplateManager": "spicebridge.template_manager",
    "generate_model": "spicebridge.model_generator",
    "GeneratedModel": "spicebridge.model_generator",
    "ModelStore": "spicebridge.model_store",
    "render_svg": "spicebridge.svg_renderer",
    "start_viewer": "spicebridge.web_viewer",
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        import importlib

        module = importlib.import_module(_LAZY_IMPORTS[name])
        return getattr(module, name)
    raise AttributeError(f"module 'spicebridge' has no attribute {name}")


__all__ = [*_LAZY_IMPORTS]
