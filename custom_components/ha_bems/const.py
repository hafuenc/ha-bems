"""Shared HA-BEMS configuration names and suggested battery settings."""

DOMAIN = "ha_bems"
SEASONAL_SOC_NAME = "Battery seasonal SOC min threshold"
DEFAULT_MONTHLY_MIN_SOC = (90, 75, 40, 30, 20, 20, 20, 20, 30, 40, 75, 90)
MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
CONF_SOURCE = "seasonal_soc_source"
CONF_MONTHLY = "monthly_min_soc_percent"
CONF_ENTITY = "seasonal_soc_entity"
SOURCE_MONTHLY = "monthly"
SOURCE_ENTITY = "entity"

CONF_MODULE = "module"
MODULE_SOC = "seasonal_soc"
MODULE_LOAD = "non_flexible_load"
MODULE_FBC = "battery_fbc"
MODULE_PV_IMPORT = "pv_forecast_import"
CONF_PV_IMPORT = "pv_import_entry_id"
