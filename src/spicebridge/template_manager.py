"""Template library for pre-made circuit netlists."""

from __future__ import annotations

import importlib.resources
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Template:
    """A circuit template loaded from JSON."""

    id: str
    name: str
    category: str
    description: str
    design_equations: list[str]
    netlist: str
    components: dict[str, dict]
    source: str  # "built-in" or "user"
    ports: dict[str, str] | None = None


class TemplateManager:
    """Discover and load circuit templates from built-in and user directories."""

    def __init__(self) -> None:
        self._templates: dict[str, Template] | None = None

    @staticmethod
    def _builtin_dir() -> Path:
        return importlib.resources.files("spicebridge") / "templates"

    @staticmethod
    def _user_dir() -> Path:
        d = Path.home() / ".spicebridge" / "templates"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_file(self, path: Path, source: str) -> Template | None:
        try:
            data = json.loads(path.read_text())
            return Template(
                id=data["id"],
                name=data["name"],
                category=data["category"],
                description=data["description"],
                design_equations=data.get("design_equations", []),
                netlist=data["netlist"],
                components=data.get("components", {}),
                source=source,
                ports=data.get("ports"),
            )
        except Exception:
            return None

    def _load_all(self) -> dict[str, Template]:
        templates: dict[str, Template] = {}

        # Built-in templates first
        builtin = self._builtin_dir()
        if builtin.is_dir():
            for p in sorted(Path(builtin).glob("*.json")):
                t = self._load_file(p, "built-in")
                if t is not None:
                    templates[t.id] = t

        # User templates override built-ins
        user = self._user_dir()
        if user.is_dir():
            for p in sorted(user.glob("*.json")):
                t = self._load_file(p, "user")
                if t is not None:
                    templates[t.id] = t

        return templates

    def _ensure_loaded(self) -> None:
        if self._templates is None:
            self._templates = self._load_all()

    def reload(self) -> None:
        """Force reload all templates."""
        self._templates = self._load_all()

    def list_templates(self, category: str | None = None) -> list[dict]:
        """Return summary dicts of all templates, optionally filtered by category."""
        self._ensure_loaded()
        assert self._templates is not None
        result = []
        for t in self._templates.values():
            if category is not None and t.category != category:
                continue
            result.append(
                {
                    "id": t.id,
                    "name": t.name,
                    "category": t.category,
                    "description": t.description,
                    "source": t.source,
                }
            )
        return result

    def get_template(self, template_id: str) -> Template:
        """Get a template by ID. Raises KeyError if not found."""
        self._ensure_loaded()
        assert self._templates is not None
        if template_id not in self._templates:
            raise KeyError(f"Template '{template_id}' not found")
        return self._templates[template_id]


# --- Standalone helper functions ---

_PARAM_RE = re.compile(
    r"^(\s*\.param\s+)(\w+)\s*=\s*(\S+)",
    re.IGNORECASE,
)


def substitute_params(netlist: str, params: dict[str, str]) -> str:
    """Rewrite .param lines to apply overrides.

    For each key in *params*, if the netlist contains a `.param KEY=oldval`
    line, replace `oldval` with the new value.  Other lines are untouched.
    """
    if not params:
        return netlist

    lines = []
    for line in netlist.splitlines():
        m = _PARAM_RE.match(line)
        if m:
            key = m.group(2)
            if key in params:
                line = f"{m.group(1)}{key}={params[key]}"
        lines.append(line)
    return "\n".join(lines)


def modify_component_in_netlist(netlist: str, component: str, value: str) -> str:
    """Modify a component value in a netlist.

    1. If *component* matches a `.param` key, update that `.param` line.
    2. Otherwise look for an instance line starting with *component* and
       replace its last token (the value field).
    3. Raises ValueError if *component* is not found in the netlist.
    """
    # Try .param line first
    found = False
    lines = netlist.splitlines()
    result = []
    for line in lines:
        m = _PARAM_RE.match(line)
        if m and m.group(2) == component:
            line = f"{m.group(1)}{component}={value}"
            found = True
        result.append(line)

    if found:
        return "\n".join(result)

    # Try component instance line (e.g. "R1 in out 1k")
    comp_re = re.compile(
        rf"^(\s*{re.escape(component)}\s+.+\s+)\S+\s*$",
        re.IGNORECASE,
    )
    result = []
    for line in lines:
        if not found and comp_re.match(line):
            line = comp_re.sub(rf"\g<1>{value}", line)
            found = True
        result.append(line)

    if not found:
        raise ValueError(f"Component '{component}' not found in netlist")

    return "\n".join(result)
