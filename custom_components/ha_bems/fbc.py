"""Pure FBC calculation ported from matlab_reference/fbc_original_excerpt.m.

Based on PVprog 1.1, Bergner, Weniger and Tjaden, HTW Berlin (2016),
with Florian Wenig's DC forecast and monthly SOC threshold adaptations.
No Home Assistant access or device writes are performed here.
"""

from dataclasses import dataclass
from math import floor, isfinite
from typing import Sequence

from .const import DEFAULT_MONTHLY_MIN_SOC
from .seasonal_soc import validate_threshold


@dataclass(frozen=True)
class FBCSettings:
    """Battery settings in the same units and SOC basis as the MATLAB code."""

    capacity_kwh: float
    target_soc_percent: float
    charge_efficiency: float
    max_charge_power_w: float
    monthly_min_soc_percent: tuple[float, ...] = DEFAULT_MONTHLY_MIN_SOC
    # Separate from max_charge_power_w, as in the original POST expression.
    output_limit_w: float = 5000.0


@dataclass(frozen=True)
class FBCResult:
    """Calculation details; only charge_limit_w corresponds to the legacy write."""

    synthetic_limit_w: float
    required_energy_kwh: float
    energy_above_limit_kwh: float
    charge_profile_w: tuple[float, ...]
    charge_limit_w: float
    limitation_disabled: bool
    override_reasons: tuple[str, ...]
    seasonal_soc_min_threshold_percent: float


def _nonnegative_finite(name: str, value: float) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


def _soc(name: str, value: float) -> None:
    _nonnegative_finite(name, value)
    if value > 100:
        raise ValueError(f"{name} must be between 0 and 100 percent")


def _round_nonnegative(value: float, digits: int = 0) -> float:
    """Round nonnegative values with halfway cases away from zero, like MATLAB."""
    scale = 10**digits
    return floor(value * scale + 0.5) / scale


def calculate_fbc(
    pv_dc_w: Sequence[float],
    *,
    step_hours: float,
    current_soc_percent: float,
    current_month: int,
    settings: FBCSettings,
    seasonal_soc_min_threshold_percent: float | None = None,
) -> FBCResult:
    """Calculate the legacy FBC profile for a prepared, equally spaced horizon.

    pv_dc_w contains interval-average DC PV power in watts, starting at the
    current control interval and ending at the selected local day-end horizon.
    The caller handles timestamps, horizon selection and forecast freshness.
    current_month is the current local calendar month (1..12), as in MATLAB.
    seasonal_soc_min_threshold_percent can supply the shared threshold resolved
    from an existing entity. If omitted, the configured monthly schedule is used.

    Invalid inputs raise ValueError and produce no recommendation. This is an
    explicit boundary around inputs for which the legacy fragment is undefined;
    deciding a device fallback belongs to the future HA control layer.
    """
    forecast = tuple(pv_dc_w)
    if not forecast:
        raise ValueError("pv_dc_w must contain at least one interval")
    for value in forecast:
        _nonnegative_finite("PV DC power", value)
    if not isfinite(step_hours) or step_hours <= 0:
        raise ValueError("step_hours must be finite and positive")
    _soc("current_soc_percent", current_soc_percent)
    _soc("target_soc_percent", settings.target_soc_percent)
    if not isinstance(current_month, int) or not 1 <= current_month <= 12:
        raise ValueError("current_month must be an integer from 1 to 12")
    if not isfinite(settings.capacity_kwh) or settings.capacity_kwh <= 0:
        raise ValueError("capacity_kwh must be finite and positive")
    if not isfinite(settings.charge_efficiency) or not 0 < settings.charge_efficiency <= 1:
        raise ValueError("charge_efficiency must be a factor greater than 0 and at most 1")
    _nonnegative_finite("max_charge_power_w", settings.max_charge_power_w)
    _nonnegative_finite("output_limit_w", settings.output_limit_w)
    if settings.output_limit_w < settings.max_charge_power_w:
        raise ValueError("Device output cap must not be lower than maximum charging power")
    if len(settings.monthly_min_soc_percent) != 12:
        raise ValueError("monthly_min_soc_percent must contain 12 values, January to December")
    for value in settings.monthly_min_soc_percent:
        _soc("monthly minimum SOC", value)

    active_threshold = validate_threshold(
        settings.monthly_min_soc_percent[current_month - 1]
        if seasonal_soc_min_threshold_percent is None
        else seasonal_soc_min_threshold_percent
    )

    required_energy = (
        max(0.0, settings.target_soc_percent - current_soc_percent)
        / 100
        * settings.capacity_kwh
        / settings.charge_efficiency
    )

    # Match MATLAB's 0:10:max(P_dc) and first minimum on an exact tie.
    # Evaluate one threshold at a time rather than allocating a full matrix.
    best_mismatch = float("inf")
    selected_limit = 0.0
    selected_energy = 0.0
    for threshold in range(0, floor(max(forecast) / 10) * 10 + 1, 10):
        energy = sum(max(0.0, power / 1000 - threshold / 1000) * step_hours for power in forecast)
        mismatch = abs(energy - required_energy)
        if mismatch < best_mismatch:
            best_mismatch = mismatch
            selected_limit = float(threshold)
            selected_energy = energy

    profile = tuple(_round_nonnegative(max(0.0, power - selected_limit), 1) for power in forecast)
    reasons: list[str] = []
    if current_soc_percent <= active_threshold:
        profile = (settings.max_charge_power_w,) * len(forecast)
        reasons.append("at_or_below_seasonal_soc_min_threshold")

    # Deliberately compare AFTER the monthly override, as the original does.
    if _round_nonnegative(profile[0]) == _round_nonnegative(forecast[0]):
        profile = (settings.max_charge_power_w,) * len(forecast)
        reasons.append("first_charge_equals_first_pv")

    return FBCResult(
        synthetic_limit_w=selected_limit,
        required_energy_kwh=required_energy,
        energy_above_limit_kwh=selected_energy,
        charge_profile_w=profile,
        charge_limit_w=max(0.0, min(profile[0], settings.output_limit_w)),
        limitation_disabled=bool(reasons),
        override_reasons=tuple(reasons),
        seasonal_soc_min_threshold_percent=active_threshold,
    )
