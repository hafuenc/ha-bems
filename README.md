# HA-BEMS

Home Assistant Building Energy Management System.

HA-BEMS is planned as a modular Home Assistant integration. The first development
milestone is forecast-based battery charging (FBC), based on the existing MATLAB
calculation. Heat-pump and energy-community (EEG) control are planned for later.

## Current status

The Python FBC calculation is available for manual result review. The initial
Home Assistant integration provides setup, settings and a shared **Battery
seasonal SOC min threshold** sensor, using a monthly schedule or an existing
entity. See [seasonal SOC setup](docs/seasonal_soc.md).

FBC forecast input and device control are not connected yet. The existing MATLAB
automation remains the operational controller. See [FBC notes](docs/fbc.md).

## Initial scope

- Port the FBC calculation to Python and compare it with MATLAB on identical inputs.
- Use a DC PV forecast, current battery state of charge and battery parameters.
- Calculate a recommended maximum battery charging power.
- Add Home Assistant configuration, status and diagnostics.
- Validate recommendations in observation mode before enabling device control.

The initial FBC implementation requires no MILP solver. Heat-pump control, EEG
control and other optional modules will be developed separately in later stages.

## Reference files

- `matlab_reference/fbc_original_excerpt.m`: original FBC reference, including
  attribution to the HTW Berlin PVprog algorithm and the author's adaptations.
- `matlab_reference/fcn_solver_gurobi.m`: reference for later optimization work;
  outside the initial FBC implementation.

The MATLAB references are preserved unchanged. Redistribution rights and an
appropriate project license must be clarified before public release.

## Planned integration location

The Home Assistant integration will live in `custom_components/ha_bems/`.
It currently contains the independent FBC calculation and the first shared
battery threshold setup and sensor. The HA setup has not been verified in a
running Home Assistant instance yet.

## Development data

Keep credentials, Home Assistant tokens, local configuration and private
measurement data outside version control. `PROJECT_CONTEXT.txt` is a local
handover and is intentionally excluded from the repository.
