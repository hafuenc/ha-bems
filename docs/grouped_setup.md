# Shared service devices and migration

HA-BEMS 0.7.0 uses **one configuration entry**. Two virtual service devices group
related entities, without per-item category entries or per-helper devices:

```text
HA-BEMS
  BEMS forecast importer [service device]
    User-named PV AC/DC input statuses
    User-named building-load input statuses
    User-named import/export price input statuses
    User-named outdoor-temperature input statuses
  BEMS entity creator [service device]
    User-named seasonal SOC thresholds
    User-named non-flexible load forecasts
    User-named thermal runtime-cost forecasts
  Individual operation-planner devices
    User-named FBC batteries
```

External forecast sensors stay with their supplying integrations. The shared
importer device owns BEMS status entities; it does not take ownership of the
original source sensors or duplicate their full forecast lists. The creator
device owns the entities calculated by BEMS. Each item retains independent
parameters and can be created multiple times.

Thermal runtime costs use one generic procedure with configurable COP and other
system parameters. A future thermal operation planner remains open; no separate
DHW/UFH setup modes or extra planner layers are introduced.

## Create and configure

1. Add HA-BEMS once. It creates the installation and both service devices.
2. Open **HA-BEMS → Configure** on its integration entry.
3. Choose **Add forecast import**, **Create helper entity** or **Add operation
   planner**, then select the item type, name it and complete setup/validation.
4. Choose **Configure an item** to select an existing item, rename or
   enable/disable it, and edit its settings. Disabling saves immediately even
   when a selected source is unavailable.
5. Choose **Remove an item** to select and confirm removal. Only that item's
   configuration and BEMS entities are removed. External source sensors, shared
   service devices and other items remain. Dependent planners report a removed
   input unavailable until another input is selected.

Changes reload the single BEMS installation briefly. FBC's enable switches
restore their previous state independently. Adding the integration again is
blocked, since additional items belong in the existing installation.

## Existing installations

The integration requires **Home Assistant 2026.10 or newer**. After updating
through HACS, restart HA to load the new code and migrate the stored layout.

Both standalone entries and the category/subentry layout are supported as
upgrade sources. The migration first captures every item and a resumable
ownership plan, then moves registry ownership before retiring old subentries and
entries. An interrupted move can be retried on the next startup.

Preserved information includes item names and parameters, disabled settings,
entity IDs and unique IDs, PV/load/price/temperature selections, stable input
references, and FBC device identities and enable-switch state. Input consumers
therefore reconnect without needing their selections recreated.

The old individual importer/helper devices are consolidated into the two shared
service devices. Their individual device IDs are retired. Any explicit
entity-area assignments are retained; inherited old device areas are copied onto
the entities before consolidation. User overrides on entity names remain.
Automations targeting an old per-helper **device** must use its preserved entity
IDs or the appropriate new shared device. Battery-planner devices retain their
existing IDs and device settings.

Automated checks cover old-layout migration, interrupted-move recovery, shared
device ownership, independent add/edit/disable/remove operations, stable input
references and multiple cost forecasts. The final integration-page appearance
can be checked after installing the release.
