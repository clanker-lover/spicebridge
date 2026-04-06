# model_generator.py

**Source:** `src/spicebridge/model_generator.py`

## Purpose

Generates SPICE model text from datasheet parameters. Pure model-text generation with no file I/O. Supports op-amp (behavioral subcircuit), BJT, MOSFET, and diode models.

## Public API

- **`generate_model(component_type, name, parameters=None)`**: Main entry point. Returns a `GeneratedModel`. Supported types: `"opamp"`, `"bjt"`, `"mosfet"`, `"diode"`.
- **`list_component_types()`**: Returns sorted list of supported type strings.
- **`get_default_parameters(component_type)`**: Returns default parameter dict for a type.

## Key Types

- **`GeneratedModel`** dataclass: `name`, `component_type`, `spice_text`, `parameters`, `metadata`, `notes`.

## Model Details

**Op-Amp** (behavioral subcircuit): Models input impedance, input bias/offset, CMRR, dominant pole with slew rate limiting, output clamping, and quiescent supply current. PSRR and Vos drift are noted but not modeled. Key defaults: GBW=10MHz, DC gain=100dB, slew=20V/us.

**BJT** (`.model NPN/PNP`): Standard Gummel-Poon parameters -- BF, IS, VAF, CJE, CJC, TF, RB, RC, RE.

**MOSFET** (Level 1 `.model NMOS/PMOS`): VTO, KP, LAMBDA, CBD, CBS, CGSO, CGDO. PMOS default Vth is auto-negated.

**Diode** (`.model D`): IS, N, BV, RS, CJO, TT.

## Validation

Model names must match `^[A-Za-z]\w*$` (start with letter, alphanumeric + underscore).

## Dependencies

`math`, `re`, `dataclasses`. No spicebridge imports.

## Architecture Role

Model generation layer. Called by [server.py](server.md) `create_model` tool. Models are persisted by [model_store.py](model_store.md). See [model-library](../concepts/model-library.md).
