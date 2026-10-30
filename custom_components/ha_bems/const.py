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
MODULE_LOAD_IMPORT = "load_forecast_import"
MODULE_IMPORT_PRICE = "electricity_import_price_forecast"
MODULE_EXPORT_PRICE = "electricity_export_price_forecast"
MODULE_OUTDOOR_TEMP = "outdoor_temperature_forecast"
MODULE_THERMAL_COST = "thermal_runtime_cost"
MODULE_THERMAL_PLAN = "thermal_storage_milp"
MODULE_OPERATION_IMPORT = "operation_constraint_import"
CONF_IMPORT_PRICE = "import_price_entry_id"
CONF_EXPORT_PRICE = "export_price_entry_id"
CONF_TEMPERATURE = "temperature_entry_id"
FORECAST_IMPORT_MODULES = (
    MODULE_PV_IMPORT, MODULE_LOAD_IMPORT, MODULE_IMPORT_PRICE,
    MODULE_EXPORT_PRICE, MODULE_OUTDOOR_TEMP,
    MODULE_OPERATION_IMPORT,
)
CONF_PV_POWER_TYPE = "pv_power_type"
CONF_PV_IMPORT = "pv_import_entry_id"
CONF_LOAD_INPUT = "load_forecast_input"
CONF_GROUP = "group"
CONF_ITEM_ID = "item_id"
CONF_ENABLED = "enabled"
CONF_LAYOUT = "layout"
LAYOUT_FLAT = "flat_devices"
LAYOUT_SPLIT = "separate_entries"
CONF_ITEMS = "items"
GROUP_CREATOR = "entity_creator"
GROUP_IMPORTER = "entity_importer"
GROUP_ELECTRICAL = "electrical_storage"
GROUP_THERMAL = "thermal_storage"
GROUP_TITLES = {
    GROUP_CREATOR: "BEMS entity creator",
    GROUP_IMPORTER: "BEMS forecast importer",
    GROUP_ELECTRICAL: "Electrical storage management",
    GROUP_THERMAL: "Thermal storage management",
}
GROUP_MODULES = {
    GROUP_CREATOR: (MODULE_SOC, MODULE_LOAD, MODULE_THERMAL_COST),
    GROUP_IMPORTER: FORECAST_IMPORT_MODULES,
    GROUP_ELECTRICAL: (MODULE_FBC,),
    GROUP_THERMAL: (MODULE_THERMAL_PLAN,),
}

# Values are the subdivision codes used by the holidays library and saved entries.
HOLIDAY_REGIONS = {
    "AT": {
        "1": "Burgenland", "2": "Kärnten", "3": "Niederösterreich",
        "4": "Oberösterreich", "5": "Salzburg", "6": "Steiermark",
        "7": "Tirol", "8": "Vorarlberg", "9": "Wien",
    },
    "DE": {
        "BW": "Baden-Württemberg", "BY": "Bayern", "BE": "Berlin",
        "BB": "Brandenburg", "HB": "Bremen", "HH": "Hamburg",
        "HE": "Hessen", "MV": "Mecklenburg-Vorpommern", "NI": "Niedersachsen",
        "NW": "Nordrhein-Westfalen", "RP": "Rheinland-Pfalz", "SL": "Saarland",
        "SN": "Sachsen", "ST": "Sachsen-Anhalt", "SH": "Schleswig-Holstein",
        "TH": "Thüringen",
    },
}
