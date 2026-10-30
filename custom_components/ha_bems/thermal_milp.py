"""Feed-forward thermal scheduling with operation/start/stop binary variables."""

from math import ceil, isfinite
from time import monotonic

from .thermal_cost import cop_points, interpolate_cop, number


def validate_plan_settings(config):
    low = number(config.get("storage_min_h", 0), "Minimum storage duration", nonnegative=True)
    high = number(config.get("storage_duration_h"), "Storage duration", positive=True)
    if low >= high:
        raise ValueError("Maximum storage duration must exceed the minimum")
    initial = config.get("initial_storage_h")
    if initial is not None and not low <= number(initial, "Initial storage duration") <= high:
        raise ValueError("Initial storage duration must lie within the storage limits")
    for key in ("min_on_h", "min_off_h"):
        if not 0 <= number(config.get(key, 0), key) <= 72:
            raise ValueError("Minimum on/off times must be between 0 and 72 hours")
    model = config.get("runtime_model")
    if model == "constant":
        values = [config.get("runtime_h_per_24h")]
    elif model == "lookup":
        points = config.get("runtime_points")
        # Reuse temperature interpolation validation; runtime may also be zero.
        if not isinstance(points, list) or not 2 <= len(points) <= 10:
            raise ValueError("Define 2–10 runtime reference points")
        cop_points([{"temperature_c": p["temperature_c"], "cop": 1} for p in points])
        values = [p["runtime_h_per_24h"] for p in points]
        values += [config.get("runtime_min_h", 0), config.get("runtime_max_h", 24)]
        if values[-2] > values[-1]:
            raise ValueError("Minimum required runtime must not exceed the maximum")
        if config.get("heating_cutoff_enabled", True):
            number(config.get("heating_cutoff_c", 20), "Heating cutoff")
    else:
        raise ValueError("Choose a runtime model")
    for value in values:
        if not 0 <= number(value, "Required runtime") <= 24:
            raise ValueError("Required runtimes must be between 0 and 24 h/24h")
    if not 1 <= number(config.get("solver_time_limit_s", 20), "Solver time limit") <= 120:
        raise ValueError("Solver time limit must be between 1 and 120 seconds")


def required_runtimes(config, temperatures, count):
    validate_plan_settings(config)
    if config["runtime_model"] == "constant":
        return [float(config["runtime_h_per_24h"])] * count
    if temperatures is None or len(temperatures) != count:
        raise ValueError("A complete outdoor temperature forecast is required")
    points = sorted((p["temperature_c"], p["runtime_h_per_24h"]) for p in config["runtime_points"])
    result = []
    for temperature in temperatures:
        number(temperature, "Outdoor temperature")
        runtime = min(config.get("runtime_max_h", 24), max(config.get("runtime_min_h", 0), interpolate_cop(temperature, points)))
        if config.get("heating_cutoff_enabled", True) and temperature > config.get("heating_cutoff_c", 20):
            runtime = 0
        result.append(runtime)
    return result


def build_matrices(costs, runtimes, durations, initial, low, high, min_on, min_off,
                   previous_operation=0, previous_elapsed_h=72, allowed=None, fixed_first=None,
                   terminal_min=None):
    """Port the supplied A_10/A_21/A_22/A_23 formulation to sparse HiGHS rows.

    Costs are already EUR/timestep. The first partial timestep is prorated;
    cumulative storage constraints include the final storage boundary.
    """
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint
    from scipy.sparse import coo_matrix

    n = len(costs)
    if n == 0 or len(runtimes) != n or len(durations) != n:
        raise ValueError("Costs, runtimes and timestep durations must have equal lengths")
    if allowed is None:
        allowed = [1] * n
    if len(allowed) != n or any(type(v) not in (int, float) or v not in (0, 1) for v in allowed):
        raise ValueError("Operation permissions must be exactly 0 or 1")
    for cost, runtime, duration in zip(costs, runtimes, durations):
        number(cost, "Cost")
        if not 0 <= number(runtime, "Runtime") <= 24 or not 0 < number(duration, "Timestep duration") <= .25:
            raise ValueError("Invalid runtime or timestep duration")
    rows, cols, coefficients, lower, upper = [], [], [], [], []

    def add(values, lb=-np.inf, ub=np.inf):
        row = len(lower)
        for col, value in values:
            rows.append(row); cols.append(col); coefficients.append(value)
        lower.append(lb); upper.append(ub)

    demand = 0
    cumulative = []
    starts = np.r_[0., np.cumsum(durations)[:-1]]
    effective_on = ceil(min_on * 4 - 1e-9) / 4
    effective_off = ceil(min_off * 4 - 1e-9) / 4
    lb = np.zeros(3 * n)
    ub = np.r_[allowed, np.ones(2 * n)]
    if fixed_first is not None:
        lb[0] = fixed_first
        ub[0] = min(ub[0], fixed_first)
    if previous_operation not in (0, 1):
        raise ValueError("Previous operation must be binary")
    for j in range(n):
        cumulative.append((j, durations[j]))
        demand += runtimes[j] / 24 * durations[j]
        add(cumulative, low - initial + demand, high - initial + demand)
        transitions = [(j, 1), (n + j, -1), (2 * n + j, 1)]
        if j:
            transitions.append((j - 1, -1))
        rhs = previous_operation if j == 0 else 0
        add(transitions, rhs, rhs)
        # Prevent simultaneous artificial start and stop edges.
        add([(n + j, 1), (2 * n + j, 1)], ub=1)
        for minimum, offset, sign, bound, history in (
            (effective_on, n, -1, 0, previous_operation),
            (effective_off, 2 * n, 1, 1, 1 - previous_operation),
        ):
            recent = [(offset + k, 1) for k in range(j + 1) if starts[j] - starts[k] < minimum - 1e-9]
            residual = history if previous_elapsed_h + starts[j] < minimum - 1e-9 else 0
            add([(j, sign), *recent], ub=bound - residual)
    if terminal_min is not None:
        add(cumulative, lb=terminal_min - initial + demand)
    if np.any(lb > ub):
        raise ValueError("An operation constraint blocks the committed current operation")
    matrix = coo_matrix((coefficients, (rows, cols)), shape=(len(lower), 3 * n)).tocsc()
    objective = np.r_[[c * dt / .25 for c, dt in zip(costs, durations)], np.zeros(2 * n)]
    return objective, Bounds(lb, ub), LinearConstraint(matrix, lower, upper), effective_on, effective_off


def solve_plan(costs, runtimes, durations, initial, config, previous_operation=0,
               previous_elapsed_h=72, allowed=None, fixed_first=None, terminal_min=None):
    import numpy as np
    from scipy.optimize import milp

    start = monotonic()
    objective, bounds, constraints, effective_on, effective_off = build_matrices(
        costs, runtimes, durations, initial, config.get("storage_min_h", 0), config["storage_duration_h"],
        config.get("min_on_h", 0), config.get("min_off_h", 0), previous_operation,
        previous_elapsed_h, allowed, fixed_first, terminal_min)
    result = milp(objective, integrality=np.ones(len(objective)), bounds=bounds,
                  constraints=constraints, options={"time_limit": config.get("solver_time_limit_s", 20), "mip_rel_gap": .01})
    if result.status not in (0, 1) or result.x is None:
        raise ValueError("No feasible thermal operating plan: " + result.message)
    candidate = np.rint(result.x)
    activity = constraints.A @ candidate
    if (not np.all(np.isfinite(result.x)) or np.max(np.abs(candidate - result.x)) > 1e-5
            or np.any(candidate < bounds.lb - 1e-7) or np.any(candidate > bounds.ub + 1e-7)
            or np.any(activity < constraints.lb - 1e-7) or np.any(activity > constraints.ub + 1e-7)):
        raise ValueError("Solver result failed constraint verification")
    operations = candidate[:len(costs)].astype(int).tolist()
    storage = [initial]
    for operation, demand, duration in zip(operations, runtimes, durations):
        level = storage[-1] + (operation - demand / 24) * duration
        for bound in (config.get("storage_min_h", 0), config["storage_duration_h"]):
            if abs(level - bound) < 1e-7:
                level = bound
        storage.append(level)
    gap = getattr(result, "mip_gap", None)
    return {"operations": operations, "storage": storage, "total_cost_eur": float(objective @ candidate),
            "solver_status": "optimal" if result.status == 0 else "feasible_time_limit",
            "solver_gap": float(gap) if gap is not None and isfinite(gap) else None,
            "solver_seconds": monotonic() - start,
            "solver_relative_gap_limit": .01,
            "effective_min_on_h": effective_on, "effective_min_off_h": effective_off}
