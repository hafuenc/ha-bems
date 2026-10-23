"""Shared seasonal battery threshold resolution, independent of HA."""

from math import isfinite

from .const import CONF_ENTITY, CONF_MONTHLY, CONF_SOURCE, DEFAULT_MONTHLY_MIN_SOC, SOURCE_MONTHLY


def validate_threshold(value: object) -> float:
    """Accept a finite percentage; never coerce missing values to zero."""
    if isinstance(value, bool):
        raise ValueError("SOC threshold must be a number between 0 and 100")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("SOC threshold must be a number between 0 and 100") from None
    if not isfinite(number) or not 0 <= number <= 100:
        raise ValueError("SOC threshold must be a number between 0 and 100")
    return number


def resolve_threshold(config: dict, month: int, entity_state=None, entity_unit=None) -> float:
    """Resolve a schedule or the current state of the configured source entity."""
    if config.get(CONF_SOURCE, SOURCE_MONTHLY) == SOURCE_MONTHLY:
        values = config.get(CONF_MONTHLY, DEFAULT_MONTHLY_MIN_SOC)
        if len(values) != 12 or not 1 <= month <= 12:
            raise ValueError("A monthly schedule requires 12 percentages and a valid month")
        validated = tuple(validate_threshold(value) for value in values)
        return validated[month - 1]
    if config.get(CONF_SOURCE) != "entity" or not config.get(CONF_ENTITY):
        raise ValueError("Invalid seasonal SOC threshold source")
    if entity_unit != "%":
        raise ValueError("The source entity must supply a percentage with unit %")
    return validate_threshold(entity_state)
