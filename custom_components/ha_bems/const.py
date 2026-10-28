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
CONF_PV_IMPORT = "pv_import_entry_id"
CONF_LOAD_INPUT = "load_forecast_input"

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
