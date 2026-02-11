"""Circuit state management for SPICEBridge sessions."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CircuitState:
    """State for a single circuit."""

    circuit_id: str
    netlist: str
    output_dir: Path
    last_results: dict | None = field(default=None)
    ports: dict[str, str] | None = field(default=None)


class CircuitManager:
    """Manage multiple circuit states."""

    def __init__(self) -> None:
        self._circuits: dict[str, CircuitState] = {}

    def create(self, netlist: str) -> str:
        """Create a new circuit and return its ID."""
        circuit_id = uuid.uuid4().hex[:8]
        output_dir = Path(tempfile.mkdtemp(prefix=f"spicebridge_{circuit_id}_"))
        self._circuits[circuit_id] = CircuitState(
            circuit_id=circuit_id,
            netlist=netlist,
            output_dir=output_dir,
        )
        return circuit_id

    def get(self, circuit_id: str) -> CircuitState:
        """Get circuit state by ID. Raises KeyError if not found."""
        if circuit_id not in self._circuits:
            raise KeyError(f"Circuit '{circuit_id}' not found")
        return self._circuits[circuit_id]

    def update_results(self, circuit_id: str, results: dict) -> None:
        """Store simulation results for a circuit."""
        self.get(circuit_id).last_results = results

    def update_netlist(self, circuit_id: str, netlist: str) -> None:
        """Replace the stored netlist for a circuit."""
        self.get(circuit_id).netlist = netlist

    def set_ports(self, circuit_id: str, ports: dict[str, str]) -> None:
        """Store port definitions for a circuit."""
        self.get(circuit_id).ports = ports

    def get_ports(self, circuit_id: str) -> dict[str, str] | None:
        """Return port definitions for a circuit, or None if not set."""
        return self.get(circuit_id).ports

    def list_all(self) -> list[dict]:
        """Return summary info for all stored circuits."""
        return [
            {
                "circuit_id": cid,
                "has_results": state.last_results is not None,
            }
            for cid, state in self._circuits.items()
        ]
