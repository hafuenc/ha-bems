"""Shared identity for the single visible Optimization Manager."""

from .const import DOMAIN


def device_info():
    return {"identifiers": {(DOMAIN, "optimization_manager")}, "name": "Optimization Manager",
            "manufacturer": "HA-BEMS", "model": "Optimization Manager"}
