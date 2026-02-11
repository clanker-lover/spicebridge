"""Persistent storage for generated SPICE model libraries.

Models are saved as individual ``.lib`` files alongside a JSON index
in ``~/.spicebridge/models/`` (configurable for testing).
"""

from __future__ import annotations

import json
from pathlib import Path

from spicebridge.model_generator import GeneratedModel
from spicebridge.sanitize import safe_path


class ModelStore:
    """Save, load, list, and delete generated SPICE models."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or Path.home() / ".spicebridge" / "models"
        self._index: dict | None = None  # lazy-loaded cache

    @property
    def base_dir(self) -> Path:
        """The root directory where model .lib files are stored."""
        return self._base_dir

    # -- internal helpers ---------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self._base_dir / "index.json"

    def _ensure_dir(self) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _load_index(self) -> dict:
        if self._index is not None:
            return self._index
        if self._index_path.exists():
            self._index = json.loads(self._index_path.read_text())
        else:
            self._index = {}
        return self._index

    def _flush_index(self) -> None:
        self._ensure_dir()
        self._index_path.write_text(json.dumps(self._index, indent=2) + "\n")

    # -- public API ---------------------------------------------------------

    def save(self, model: GeneratedModel) -> Path:
        """Write *model* to a ``.lib`` file and update the index.

        Overwrites any existing model with the same name.
        Returns the absolute path to the ``.lib`` file.
        """
        self._ensure_dir()
        lib_path = safe_path(self._base_dir, f"{model.name}.lib")
        lib_path.write_text(model.spice_text)

        index = self._load_index()
        index[model.name] = {
            "component_type": model.component_type,
            "parameters": model.parameters,
            "metadata": model.metadata,
            "notes": model.notes,
        }
        self._flush_index()
        return lib_path

    def load(self, name: str) -> tuple[str, dict]:
        """Return ``(spice_text, index_entry)`` for the named model.

        Raises ``KeyError`` if the model does not exist.
        """
        index = self._load_index()
        if name not in index:
            raise KeyError(f"Model '{name}' not found")
        lib_path = safe_path(self._base_dir, f"{name}.lib")
        if not lib_path.exists():
            raise KeyError(
                f"Model '{name}' index entry exists but .lib file is missing"
            )
        return lib_path.read_text(), index[name]

    def list_models(self) -> list[dict]:
        """Return summary dicts for every saved model, sorted by name."""
        index = self._load_index()
        models = []
        for name in sorted(index):
            entry = index[name]
            models.append(
                {
                    "name": name,
                    "component_type": entry["component_type"],
                    "file_path": str(self._base_dir / f"{name}.lib"),
                    "include_statement": f".include {self._base_dir / f'{name}.lib'}",
                    "parameters": entry.get("parameters", {}),
                }
            )
        return models

    def delete(self, name: str) -> None:
        """Remove a model's ``.lib`` file and its index entry.

        Raises ``KeyError`` if the model does not exist.
        """
        index = self._load_index()
        if name not in index:
            raise KeyError(f"Model '{name}' not found")
        lib_path = safe_path(self._base_dir, f"{name}.lib")
        if lib_path.exists():
            lib_path.unlink()
        del index[name]
        self._flush_index()

    def get_lib_path(self, name: str) -> Path:
        """Return the absolute path to a model's ``.lib`` file.

        Raises ``KeyError`` if the model does not exist in the index.
        """
        index = self._load_index()
        if name not in index:
            raise KeyError(f"Model '{name}' not found")
        return safe_path(self._base_dir, f"{name}.lib")
