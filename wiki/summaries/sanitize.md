# sanitize.py

**Source:** `src/spicebridge/sanitize.py`

## Purpose

Input sanitization and security validation. Prevents SPICE directive injection, path traversal, and other input-based attacks against the simulation engine.

## Public API

- **`sanitize_netlist(netlist, _allow_includes=False)`**: Validates a netlist for dangerous directives. Checks: size limit (1MB), backtick execution, disallowed directives, `.include`/`.lib` blocking (unless `_allow_includes=True`). Reassembles continuation lines before checking.
- **`validate_component_value(value)`**: Checks for injection characters (newlines, semicolons, backticks, directive markers).
- **`safe_path(base_dir, user_input)`**: Resolves a path and ensures it stays within `base_dir`. Prevents path traversal.
- **`validate_include_paths(netlist, allowed_dirs)`**: Validates that all `.include`/`.lib` paths resolve within allowed directories.
- **`validate_filename(filename)`**: Checks for path separators and `..` in filenames.
- **`validate_format(fmt)`**: Ensures format is one of `{png, svg, pdf}`.
- **`sanitize_error(exc)`**: Strips internal filesystem paths from exception messages before sending to clients.
- **`safe_error_response(exc, logger, context)`**: Logs full exception at DEBUG, returns sanitized error dict.

## Allowed Directives

Whitelist: `ac`, `tran`, `op`, `dc`, `param`, `subckt`, `ends`, `model`, `include`, `lib`, `global`, `end`, `ic`, `nodeset`, `options`, `temp`, `save`.

## Dependencies

`re`, `pathlib`, `logging`. No spicebridge imports.

## Architecture Role

Security boundary. Called by [server.py](server.md) on every user-supplied netlist and component value. See [security-model](../concepts/security-model.md).
