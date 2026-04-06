# Multi-Stage Composition

Cross-cutting concept appearing in: `composer.py`, `server.py`, `constants.py`

## FACTS

The composition engine enables building complex circuits from simpler stages (Source: `composer.py`):

### Algorithm
1. **Label assignment**: Each stage gets a unique prefix (S1, S2, ... or user-specified).
2. **Port validation**: Every stage must have defined ports.
3. **Auto-wiring**: If no explicit connections given, output of stage N connects to input of stage N+1. Searches for ports named `out`/`vout`/`output` and `in`/`inp`/`input`/`in1`.
4. **Netlist prefixing**: All component refs and nodes in each stage are prefixed with the stage label to avoid name collisions. Node `"0"` (ground) is never prefixed.
5. **Source stripping**: When a stage receives an incoming connection, voltage/current sources on the receiving port's node are stripped (the driving stage provides the signal).
6. **Wire renaming**: Connection nodes are renamed to `wire_{from_label}_{to_label}`.
7. **Subcircuit deduplication**: Identical `.subckt` blocks are kept once; different content with same name triggers a warning.
8. **Final assembly**: Includes, subckts, stage netlists assembled with comments.

### Port Detection
`auto_detect_ports()` uses heuristic node-name matching (Source: `composer.py`):
- `in`, `inp`, `inp1-2`, `in1-3` -> input
- `out`, `vout` -> output
- `vcc`, `vdd` -> power
- `vee`, `vss` -> power
- `0`, `gnd` -> ground

## INFERENCES

The pure text-processing approach (no AST or circuit graph) makes composition fast but fragile with unusual netlist formats. The regex-based node renaming (`_rename_node`) uses word-boundary matching to avoid partial replacements.

## Related Pages

- [composer.py](../summaries/composer.md), [server.py](../summaries/server.md)
- [circuit-lifecycle](circuit-lifecycle.md) -- composed circuits become new circuit IDs
