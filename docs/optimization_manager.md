# Optimization Manager

HA-BEMS provides one **Optimization Manager** entry for the installation. Existing
installations receive it automatically on update. Configure it under Settings →
Devices & services → HA-BEMS → Optimization Manager → Configure.

The manager owns the execution sequence for all operation planners, currently
thermal MILP and FBC. Input importers, generated building load and weather
providers remain independent input services. Runtime-cost helpers are prepared
immediately before their consuming planner; other cost helpers follow the plans
they depend on. The initial sequence follows configured dependencies, with thermal
planners before independent FBC planners. Existing item and entity IDs are retained.

## Settings and controls

- **Maximum solver time per planner:** global MILP limit, default 20 seconds.
- **Total optimization time budget:** default 120 seconds. Preparation counts
  toward the budget. The remaining budget is shared across remaining MILP solves,
  capped by the per-planner limit. No new solver starts with less than one second
  available. Preparation and solver cleanup can slightly exceed the budget; late
  results are rejected. This is not a hard process-termination deadline.
- **1st operation planner, 2nd operation planner, …:** all sequence dropdowns
  appear on the same settings page, prefilled with the saved order. Every dropdown
  lists all configured planners. Saving requires each planner exactly once and
  rejects backward dependencies. Invalid choices remain visible for correction.
  Disabled planners are labelled and retain their position. New planners receive
  an additional dropdown, initially inserted in a valid position. If the planner
  list changes while the page is open, review the refreshed list before saving.
- **Recalculate now:** run the entire sequence immediately. Repeated presses while
  running are ignored and never queue additional runs. This is the explicit
  exception to the regular once-per-quarter schedule.
- **Optimization status:** `waiting`, `running`, `completed`,
  `partially_completed` or `failed`. Attributes show the current module, sequence,
  run ID, trigger, start time, duration, last fully successful run and errors.

## Clock and publication

Automatic runs start at HH:00, HH:15, HH:30 and HH:45, once per UTC quarter slot
(including across daylight-saving transitions). Input and settings changes wait
for the next tick. Startup and reload do not solve immediately. Valid saved thermal
plans are restored while waiting. First-time planners without a valid plan wait
for the next tick or an explicit Recalculate now press.

Each run captures shared input snapshots and the current entity inputs. Downstream
planners consume the newly staged upstream results, including runtime costs and
electrical load derived from thermal operation. Public entities retain their
previous plans until calculation and validation finish. Results are assigned to
all coordinators before output notifications in one event-loop turn. Each result
includes `optimization_run_id`, `optimization_slot` and `optimization_trigger`.

Connected dependency groups publish together. If one member fails, its connected
group keeps its previous plans, explicitly marked `using_previous_plan` or
`using_previous_forecast` with `refresh_error`, for at most one hour while usable.
Independent groups can still complete. Without a usable prior result the affected
outputs are unavailable. Failed speculative thermal state and cost caches are
rolled back. Failed scheduled runs wait for the next tick; manual retries remain
possible. Results crossing a quarter boundary or a configuration reload are
discarded. Successful thermal plans retain their existing persisted state.

Manual calculations use the remaining current interval and preserve operation
already committed in that interval and minimum on/off obligations. Regular runs
use full clock-aligned quarters. FBC manual bypass and seasonal SOC safeguards
continue to act immediately; they do not wait for cost optimization. A changed
bypass or battery safeguard input during calculation invalidates the pending batch.

## Dependencies and scope

The sequence defines execution order, not resource allocation. Continue selecting
operation constraints and higher-priority electrical loads in each module.
Backward dependencies and cycles are rejected, including dependencies through
runtime-cost helpers. A lower-priority planner cannot revise an earlier plan.
There is no new joint optimizer and no new battery discharge strategy.
