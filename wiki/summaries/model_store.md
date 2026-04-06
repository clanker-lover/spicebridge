# model_store.py

**Source:** `src/spicebridge/model_store.py`

## Purpose

Persistent storage for generated SPICE model libraries. Saves models as individual `.lib` files alongside a JSON index in `~/.spicebridge/models/`.

## Public API

- **`ModelStore`**: Main class.
  - `save(model)`: Writes `.lib` file and updates index. Overwrites existing models with same name. Returns absolute path to `.lib` file.
  - `load(name)`: Returns `(spice_text, index_entry)`. Raises `KeyError` if not found.
  - `list_models()`: Returns summary dicts for all saved models, sorted by name.
  - `delete(name)`: Removes `.lib` file and index entry.
  - `get_lib_path(name)`: Returns absolute path to a model's `.lib` file.
  - `base_dir`: Property returning the root storage directory.

## Storage Layout

```
~/.spicebridge/models/
  index.json          # name -> {component_type, parameters, metadata, notes}
  MyOpamp.lib         # SPICE model text
  MyBJT.lib
```

## Index Management

Lazy-loaded cache (`_index`). Flushed atomically via tmp file + `os.replace`.

## Security

Uses `safe_path()` from [sanitize.py](sanitize.md) to prevent path traversal in model names.

## Dependencies

`spicebridge.model_generator` (GeneratedModel, _validate_name), `spicebridge.sanitize` (safe_path), `json`, `os`.

## Architecture Role

Persistence layer for models. Called by [server.py](server.md) `create_model`, `list_models` tools, and `_resolve_model_includes()`. See [model-library](../concepts/model-library.md).
