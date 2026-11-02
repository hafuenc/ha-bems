# Diagnostic COP and runtime characteristics

Each thermal runtime-cost setup provides a **COP characteristic** diagnostic
sensor. Each thermal MILP planner provides a **Runtime characteristic** sensor.
Both constant and lookup models are supported. They use the same operational
evaluation functions as the cost and demand calculations, without solver runs
or extra provider refreshes. Editing the normal setup and reloading the entry
updates the characteristic.

The state is the configured function evaluated at the current quarter-hour
outdoor-temperature forecast, not a temperature measurement. Constant models
remain numeric without a temperature input. When a lookup input cannot be read,
the state is unknown unless the cost model is already using a real COP fallback.
The entity remains available so its configured curve can still be plotted.

Attributes:

- `characteristic_schema_version: 1`, `function` (`cop` or `runtime_requirement`),
  `model` (`constant` or `lookup`), `input_unit`, `output_unit`.
- `reference_points`: sorted `{temperature_c, value}` settings, empty for constant models.
- `curve_points`: ordered `{temperature_c, value, raw_value, region, fallback_active}`
  samples of the effective function. `region` is `constant`, `interpolation` or
  `extrapolation`; `raw_value` is before runtime clipping/cutoff or COP fallback.
- `display_range_c` and `curve_step_c`: normally −25 to +40 °C at 0.5 °C steps,
  extended when reference points lie outside this range. Exact reference points,
  clipping intersections and both sides of a heating cutoff are included too.
- `current_temperature_c`, `current_value`, `current_raw_value`, `fallback_active`,
  `source_entity`, `status` and `reason` describe the current operating point.
- Constants expose `constant_value`. COP lookup exposes `fallback_value`.
  Runtime exposes `minimum`, `maximum`, `heating_cutoff_enabled`,
  `heating_cutoff_c` and `heating_cutoff_comparison`.

Above the heating cutoff (strictly `temperature > cutoff`), runtime is zero,
including when the configured minimum is positive. Non-positive/non-finite COP
extrapolations use the configured fallback COP and are marked in the curve.
Reference and curve lists are excluded from recorder attributes to avoid
duplicating static curves on every state update. The small numeric sensor state
can still be inspected in normal HA History.

## Lovelace chart

The optional display is bundled with HA-BEMS and served by Home Assistant.
Add this dashboard resource once as a **JavaScript module**:

```text
/ha_bems/characteristic-card.js?v=0.10.4
```

The version query avoids stale browser copies after updates. No additional HACS
card, external service or editor is required. A card can show up to eight
diagnostic sensors on the same numeric temperature axis:

```yaml
type: custom:bems-characteristic-card
title: COP vs outdoor temperature
entities:
  - entity: sensor.example_dhw_cop_characteristic
    name: DHW
    color: '#d97706'
  - entity: sensor.example_ufh_cop_characteristic
    name: UFH
    color: '#2563eb'
```

Use a separate card for runtime sensors with `y_label: Required runtime (h/24h)`.
Hollow markers show reference values, solid/dashed lines show interpolation and
extrapolation, and filled markers show current values. COP fallback segments are
red. The card is read-only: settings remain in the integration's normal HA flow.
Add a relative History link below each chart selecting all plotted entities, for
example `/history?entity_id=sensor.example_dhw_cop_characteristic,sensor.example_ufh_cop_characteristic`.
