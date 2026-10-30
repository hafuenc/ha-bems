# Thermal storage management — MILP

Open **HA-BEMS → Configure → Add operation planner → Thermal storage management
(MILP)**. Repeat the same procedure for DHW, UFH or any other named thermal system.
Each planner has its own device and settings. It publishes recommendations only;
your automations apply them to the equipment.

## Setup

1. Select an existing **EUR/timestep runtime-cost forecast**. It must use the
   same timestamped 15-minute, rolling 72-hour format as BEMS cost entities.
2. Choose the required runtime:
   - **Constant:** enter operating hours per 24 hours, between 0 and 24.
   - **Outdoor temperature dependent:** select an imported outdoor temperature
     forecast and enter 2–10 `outdoor temperature;runtime` reference points.
     The default is `-12.1;18` and `12;0`. Decimal dots and commas are accepted.
     Values interpolate/extrapolate linearly and are clamped to the chosen
     minimum and maximum runtime. The optional heating cutoff defaults to
     20 °C; strictly above it, demand is zero even if the minimum is positive.
3. Define minimum and maximum **storage duration**, in hours. The maximum
   defaults to 6.4 h and minimum to 0 h. Duration expresses capacity in equivalent
   full-output operating hours, not literal time until the tank or building is
   full. Ongoing thermal demand changes the actual filling/emptying time.
4. Optionally enter the initial storage level in hours. Leaving it empty starts
   halfway between the minimum and maximum. The model starts with operation off
   and its initial minimum off-time already satisfied. Future on/off history is
   taken exclusively from the planner's own recommendations.
5. Set minimum on/off times in hours. Values round upward to complete 15-minute
   steps; the effective times are published in the output attributes.
6. Optionally select an operation-constraint import. Zero **blocks** operation;
   one **permits** it without forcing it. The default solver limit is 20 seconds.
7. Check the feasible-plan preview and save.

**24 h/24h means continuous operation; 12 h/24h means running half the time on
average.** This is a demand rate, not a requirement to run in a particular set
of hours each day. Storage allows the optimizer to shift operation to cheaper
times while respecting its limits at every timestep.

The storage balance is:

```text
next_storage_h = storage_h + (operation - runtime_h_per_24h / 24) × timestep_h
```

The terminal storage level cannot be below the model's initial level. Negative
electricity costs are supported, but the upper storage bound still applies.
The heating cutoff sets demand to zero; it does not itself forbid operation.

## Feed-forward state and restarts

Optimization starts on the quarter-hour clock: **HH:00, HH:15, HH:30 and HH:45**.
Each planner solves at most once per slot, using the latest available inputs.
Forecast updates between those times are used at the next scheduled run.
Startup, reloads and configuration changes can calculate immediately. If inputs
are temporarily missing, their arrival can complete the pending run. A failed
solver attempt waits until the next quarter-hour instead of repeatedly solving.
The clock remains active even if an output entity is disabled.

There is **no equipment feedback**. The estimated storage level advances only
along the previously published operation plan, assuming that plan was followed.
Storage and prior on/off commitments are restored across reloads and restarts
while the saved plan covers the current time. An already-started quarter-hour
remains committed when forecasts change; minimum on/off obligations continue
across replanning, including commitments originating near a forecast's end.

On startup or reload, the first interval may be partly elapsed. Only its remaining duration is used
for storage and cost, and `effective_start` identifies the time represented.
`start`/`end` retain the standard quarter-hour interval boundaries.
Regular scheduled runs use complete 15-minute intervals.

Changing the storage/demand model restarts its estimate. The explicit **Restart
feed-forward model** setting resets it using the initial value (or the halfway
default). Use this after an outage beyond the saved plan's end; BEMS does not
invent missing operating history or silently reset to 50%.

## DHW → UFH hierarchy

1. Create the DHW planner and let it produce a valid operation plan.
2. Open **BEMS forecast importer → Operation constraint forecast**.
3. Select the DHW **operation-plan binary sensor**.
4. Choose **1 = another system running — invert to block this planner**.
5. In the UFH planner, select this imported operation constraint.

| DHW forecast operation | Imported UFH permission | UFH decision |
|---:|---:|---|
| 1 | 0 | Forced off |
| 0 | 1 | Optimizer chooses on or off |

For a source that already describes availability, choose **1 = operation allowed
— use directly**. Both interpretations consume records with numeric `operation`
values of exactly 0 or 1; boolean or fractional values are rejected. A native
binary sensor's current `on`/`off` state is accepted because numeric values come
from its forecast attributes.

The downstream planner refreshes its upstream BEMS planner first and consumes
the resulting validated permissions. Circular dependencies are rejected.
This is sequential planning: UFH cannot revise the higher-priority DHW plan.
Check that their combined runtime requirements can fit the shared equipment.
No fixed subtraction between DHW and UFH runtime settings is applied.
If a changed upstream plan conflicts with a downstream minimum on-time, the
downstream problem can be infeasible; BEMS reports that conflict instead of
breaking either constraint.

## Output entities

**Operation plan** is a binary sensor with native `on`/`off` history and numeric
forecast records:

```yaml
schema_version: 1
interval_minutes: 15
forecast_value_key: operation
forecast:
  - start: '2026-11-01T08:00:00+00:00'
    end: '2026-11-01T08:15:00+00:00'
    effective_start: '2026-11-01T08:00:00+00:00'
    operation: 1
    allowed: 1
    runtime_h_per_24h: 12
    storage_start_h: 3.2
    storage_end_h: 3.325
```

**Storage duration** is a numeric sensor in `h`, with the current estimated level
and interval records containing `duration_h` at `effective_start` and
`duration_end_h` at the end. Every interval includes both boundaries, including
the final forecast boundary. Both entities expose solver status, cost, solve
time and optimality gap; their forecast lists remain live attributes and are
excluded from recorder to keep history compact.

## Solver and failures

The planner uses **HiGHS via SciPy** in a background executor. An optimal result
or a feasible result at the time limit is accepted only after verifying the
binary decisions, operation permissions, storage and commitment constraints.
The solver's relative optimality-gap target is 1%.
No feasible solution, missing input coverage or invalid upstream plans make
the recommendation unavailable and create a persistent notification. Unknown
availability is never interpreted as permission to run. The notification is
dismissed when a valid plan is available again.

Independent cost entities still evaluate PV independently. The hierarchy
prevents simultaneous use of shared equipment; it does not allocate PV or
coordinate battery energy between otherwise independent planners.
