# Integration entries and migration

HA-BEMS 0.10.0 uses separate integration entries for BEMS forecast importer,
BEMS entity creator, and each independently named operation planner.

```text
HA-BEMS integration
  BEMS forecast importer [integration entry]
    PV AC forecast [service device, input-status entity]
    Import/export prices [individual service devices]
    Other forecasts [individual service devices]
  BEMS entity creator [integration entry]
    Non-flexible load [service device, generated entity]
    Runtime costs [service device, generated entity]
    Other helpers [individual service devices]
  FBC Battery 1 [integration entry]
    Battery planner device and its entities
  Thermal MILP - DHW [integration entry]
    Thermal planner device and its entities
  Thermal MILP - UFH [integration entry]
    Thermal planner device and its entities
```

Rows below an entry are Home Assistant devices/services. Each helper or forecast
registration gets its own virtual service device, displaying its name directly
in the overview. External forecast sensors stay with their supplying integration.
The importer owns its BEMS input-status entity, without copying or linking the
external source into that device.

## Create and configure

1. Open **Add entry** on HA-BEMS.
2. Choose **BEMS forecast importer**, **BEMS entity creator** or **Add operation
   planner**. Each service category can exist once; multiple planners are supported.
3. Open **Configure** on a service entry to add, configure or remove its items.
4. Open **Configure** on a planner entry to edit its name, enabled setting and
   module parameters. Remove a planner through the native integration-entry menu.

Changes reload the affected entry. Dependent planners use updated inputs on their
normal schedule. Thermal MILP calculates on the fixed quarter-hour clock, with
immediate startup/reload calculations. FBC switches retain their state on restart.

Runtime-cost forecasts belong to BEMS entity creator, separately from the MILP
planners consuming them. Both support independently named systems with a generic
procedure for DHW, UFH or other uses. Select an existing MILP plan directly, or
an imported external constraint forecast, and choose direct/inverted interpretation
in the consuming planner to form a one-way hierarchy.

## Existing installations

Home Assistant 2026.10 or newer is required. After updating through HACS, restart
Home Assistant. Older standalone/category layouts first pass through the legacy
migration. The former single entry is then split after startup, while destination
entries are staged without creating output entities.

Before registry ownership changes, the migration saves all item settings in a
resumable journal. The old entry becomes BEMS entity creator. Importer and planner
entries are created independently. Ownership moves before final entries load;
importers and helpers load before planners. Interrupted moves resume on restart.
Failures produce a persistent notification and an HA-BEMS log entry.

Preserved information includes:

- Entity IDs, unique IDs, history identities, user names and disabled overrides.
- Item names, settings, enabled flags and stable cross-entry forecast references.
- Existing planner device IDs and FBC switch identity/state.
- Thermal feed-forward model identity, saved estimates and on/off commitments.
- Entity area assignments and inherited service-device areas.

The two former shared service devices are replaced by per-item service devices.
Their device IDs are retired once empty. Entity references continue to work.
References explicitly targeting an old shared device need adapting to the new
per-item device or preserved entity. Planner devices retain their IDs.

Keep a pre-upgrade configuration backup: downgrading code alone does not convert
the new configuration back to the former single-entry layout.

Automated checks cover legacy migration, interrupted moves/retry, stable entity
and device identities, area assignments, independent edits, category boundaries,
multiple planners and preserved input references.
