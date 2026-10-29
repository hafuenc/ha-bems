# Grouped integration cards

HA-BEMS uses one Home Assistant configuration entry per category and native
configuration subentries for its individual items:

```text
BEMS forecast importer
  PV AC roof
  PV DC roof
  Non-flexible load forecast
  Import prices
  Export prices
  Outdoor temperature

BEMS entity creator
  Battery seasonal SOC min threshold
  Non-flexible building load profile

Electrical storage management
  FBC Battery 1
  FBC Battery 2

Thermal storage management
  DHW runtime cost
  UFH runtime cost
```

These are the top-level cards on the HA-BEMS integration page, rather than a
separate top-level card for every forecast, helper or battery. Item names remain
user-defined. Multiple instances of a helper, forecast or FBC module are allowed.

## Create and configure

1. Add the HA-BEMS integration and choose a category to create its card.
2. Inside that card, use **Add forecast**, **Add helper** or **Add storage module**.
3. Select the item type, name it, and complete its existing setup/validation.
4. Use the item's **Configure** action to rename, enable/disable or edit it.
   Enabled items continue through their type-specific settings. Disabling saves
   immediately, including when the selected source is invalid or unavailable.
5. Add the integration again only to create another category. An existing
   category cannot be created twice. Additional items go inside its existing card.

Thermal storage management offers **Add runtime cost setup**. Create multiple
named instances with current and 72-hour EUR/timestep forecasts; see
[thermal runtime costs](thermal_runtime_cost.md). Demand, storage duration and
MILP scheduling remain separate future modules. Electrical storage offers FBC.

Deleting an item removes its own entities and configuration; other items stay
configured. Removing a category removes its contained items too. Editing an item
reloads its category, so siblings briefly reload while retaining their settings.
FBC's enable switch and its off/max-power behaviour remain independent per battery.

## Existing installations

The grouped implementation requires **Home Assistant 2026.10 or newer**, including
its native subentry and device-registry ownership APIs. HACS declares this minimum.
After installing the local changes, restart Home Assistant for the migration.

On startup, HA-BEMS consolidates previous standalone entries into their category
cards. It retains each item's original identity in `item_id`, keeping entity
unique IDs, device identifiers, selected PV/load references and subscriptions
stable. It moves the existing entity/device registry ownership into subentries,
preserving entity IDs, user-assigned device names and areas. Existing disabled
entries become disabled items inside their category.
If every previous item in a category was disabled, that category also remains
disabled; enable the category before enabling individual items again.

The destination configuration is saved before registry ownership moves. Registry
moves can be retried after an interrupted startup without duplicating items.
Retired standalone entry shells are removed only after startup finishes. There is
no need to delete/recreate the existing helpers or FBC batteries.

Local checks cover category creation, item add/edit/disable, migration retry,
preserved identities/references, entity-to-subentry association, multiple FBC
switch initialization and cleanup after the last item is removed. The final
integration-page appearance still needs verification in a running Home Assistant.

Home Assistant's [native subentry documentation](https://contributors.home-assistant.io/core/integrations/subentries/)
describes the underlying grouping and individual configuration behaviour.
