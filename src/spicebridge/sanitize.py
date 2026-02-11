"""Input sanitization for SPICEBridge security.

Provides validation functions to prevent SPICE directive injection,
path traversal, and other input-based attacks.
"""

from __future__ import annotations

import re
from pathlib import Path

# Maximum netlist size: 1 MB
MAX_NETLIST_SIZE = 1_000_000

# Dangerous SPICE directives that can execute arbitrary commands
_DANGEROUS_DIRECTIVES = re.compile(
    r"^\s*\.\s*(system|exec|shell|control|endc|python|csparam)\b",
    re.IGNORECASE,
)

# .include and .lib with user-controlled paths (block all user-supplied ones)
_INCLUDE_DIRECTIVE = re.compile(
    r"^\s*\.\s*(include|lib)\b",
    re.IGNORECASE,
)

# Backtick execution
_BACKTICK = re.compile(r"`")

# Whitelist for component values: numbers, SI prefixes, expressions in braces
_COMPONENT_VALUE_RE = re.compile(r"^[A-Za-z0-9_.{}\-+*/() ]+$")


def sanitize_netlist(netlist: str, *, _allow_includes: bool = False) -> str:
    """Validate a netlist for dangerous SPICE directives.

    Args:
        netlist: The netlist string to validate.
        _allow_includes: If True, skip .include/.lib checks.
            Used internally after _resolve_model_includes has added
            trusted include lines.

    Returns:
        The netlist string (unchanged) if safe.

    Raises:
        ValueError: If the netlist contains dangerous directives or
            exceeds the size limit.
    """
    if len(netlist) > MAX_NETLIST_SIZE:
        raise ValueError(
            f"Netlist too large: {len(netlist)} chars (max {MAX_NETLIST_SIZE})"
        )

    for lineno, line in enumerate(netlist.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue

        if _DANGEROUS_DIRECTIVES.match(stripped):
            directive = stripped.split()[0]
            raise ValueError(
                f"Dangerous SPICE directive '{directive}' "
                f"on line {lineno} is not allowed"
            )

        if not _allow_includes and _INCLUDE_DIRECTIVE.match(stripped):
            directive = stripped.split()[0]
            raise ValueError(
                f"Directive '{directive}' on line {lineno} is not allowed "
                f"in user-supplied netlists. Use the 'models' parameter instead."
            )

        if _BACKTICK.search(stripped):
            raise ValueError(f"Backtick execution on line {lineno} is not allowed")

    return netlist


def validate_component_value(value: str) -> str:
    """Validate a component value string for injection attempts.

    Args:
        value: The component value to validate.

    Returns:
        The value string (unchanged) if safe.

    Raises:
        ValueError: If the value contains injection characters.
    """
    if not value:
        raise ValueError("Component value must not be empty")

    if "\n" in value or "\r" in value:
        raise ValueError("Component value must not contain newlines")

    if ";" in value:
        raise ValueError("Component value must not contain semicolons")

    if "`" in value:
        raise ValueError("Component value must not contain backticks")

    if value.lstrip().startswith("."):
        raise ValueError(
            "Component value must not start with '.' (SPICE directive marker)"
        )

    if not _COMPONENT_VALUE_RE.match(value):
        raise ValueError(
            f"Component value '{value}' contains disallowed characters. "
            f"Only alphanumerics, '.', '_', braces, arithmetic operators, "
            f"and spaces are allowed."
        )

    return value


def safe_path(base_dir: Path, user_input: str) -> Path:
    """Resolve a path and ensure it stays within base_dir.

    Args:
        base_dir: The trusted base directory.
        user_input: The user-supplied path component.

    Returns:
        The resolved path guaranteed to be under base_dir.

    Raises:
        ValueError: If the resolved path escapes base_dir.
    """
    resolved = (base_dir / user_input).resolve()
    if not resolved.is_relative_to(base_dir.resolve()):
        raise ValueError("Path traversal attempt blocked")
    return resolved


def validate_filename(filename: str) -> str:
    """Validate a filename contains no path separators or traversal.

    Args:
        filename: The filename to validate.

    Returns:
        The filename (unchanged) if safe.

    Raises:
        ValueError: If the filename is invalid.
    """
    if not filename:
        raise ValueError("Filename must not be empty")
    if "/" in filename or "\\" in filename:
        raise ValueError("Invalid filename: must not contain path separators")
    if ".." in filename:
        raise ValueError("Invalid filename: must not contain '..'")
    return filename


def validate_format(fmt: str) -> str:
    """Validate schematic output format.

    Args:
        fmt: The format string to validate.

    Returns:
        The format string (unchanged) if valid.

    Raises:
        ValueError: If the format is not in the allowed set.
    """
    allowed = {"png", "svg", "pdf"}
    if fmt not in allowed:
        raise ValueError(f"Invalid format '{fmt}': must be one of {allowed}")
    return fmt
