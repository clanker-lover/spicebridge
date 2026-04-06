# Model Library

Cross-cutting concept appearing in: `model_generator.py`, `model_store.py`, `server.py`

## FACTS

The model library enables generating and persisting custom SPICE component models from datasheet parameters (Source: `model_generator.py`, `model_store.py`):

### Generation Flow
1. User calls `create_model(component_type, name, parameters)`.
2. `generate_model()` dispatches to type-specific generator (_generate_opamp, _generate_bjt, _generate_mosfet, _generate_diode).
3. Generator applies defaults for omitted parameters, computes derived values, produces SPICE model text.
4. `ModelStore.save()` writes `.lib` file and updates JSON index at `~/.spicebridge/models/`.
5. Returns the `.include` statement for use in netlists.

### Usage in Circuits
Models can be referenced two ways (Source: `server.py`):
- Pass `models=["ModelName"]` to `create_circuit` or `load_template` -- auto-injects `.include` lines.
- Manually add `.include` lines (blocked by sanitizer unless using the `models` parameter).

### Model Types
- **Op-amp**: Behavioral `.subckt` with 5 pins (inp, inn, out, vcc, vee). Models: input impedance, bias current, offset, CMRR, dominant pole, slew rate, output clamping, supply current.
- **BJT**: Standard `.model NPN/PNP` with Gummel-Poon parameters.
- **MOSFET**: Level 1 `.model NMOS/PMOS`.
- **Diode**: Standard `.model D`.

## INFERENCES

The op-amp model is the most sophisticated -- it uses behavioral sources (Egain, Gslew, Bclamp) rather than a simple VCVS. This gives realistic frequency response and slew rate behavior. The other models (BJT, MOSFET, diode) are straightforward parameter mappings to standard SPICE model cards.

## Related Pages

- [model_generator.py](../summaries/model_generator.md), [model_store.py](../summaries/model_store.md)
- [security-model](security-model.md) -- `.include` path validation
