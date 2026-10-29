"""Explicit units and value semantics for shared forecast imports."""

from .const import (
    CONF_MODULE, CONF_PV_POWER_TYPE, MODULE_LOAD_IMPORT, MODULE_IMPORT_PRICE,
    MODULE_EXPORT_PRICE, MODULE_OUTDOOR_TEMP, MODULE_THERMAL_COST,
)


def forecast_type(config):
    """Return label, unit, record field and whether values must be nonnegative."""
    module = config.get(CONF_MODULE)
    if module == MODULE_LOAD_IMPORT:
        return "Non-flexible building load", "W", "power_w", True
    if module == MODULE_IMPORT_PRICE:
        return "Electricity import price", "EUR/kWh", "price_eur_per_kwh", False
    if module == MODULE_EXPORT_PRICE:
        return "Electricity export price", "EUR/kWh", "price_eur_per_kwh", False
    if module == MODULE_OUTDOOR_TEMP:
        return "Outdoor air temperature", "°C", "temperature_c", False
    if module == MODULE_THERMAL_COST:
        return "Thermal runtime cost", "EUR/timestep", "cost_eur_per_timestep", False
    basis = config.get(CONF_PV_POWER_TYPE, "unspecified")
    return f"PV {basis.upper() if basis in ('ac', 'dc') else '(type not specified)'}", "W", "power_w", True


def pv_input_label(entry):
    config = {**entry.data, **entry.options}
    basis = config.get(CONF_PV_POWER_TYPE, "unspecified")
    suffix = basis.upper() if basis in ("ac", "dc") else "AC/DC not specified — Configure input"
    return f"{entry.title} ({suffix})"
