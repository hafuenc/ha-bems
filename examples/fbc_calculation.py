"""Print a synthetic FBC example for manual review; these are not site settings.

Run from the repository root: python -m examples.fbc_calculation
"""

from dataclasses import asdict
import json

from custom_components.ha_bems.fbc import FBCSettings, calculate_fbc


def main() -> None:
    # Illustrative hourly DC PV forecast and battery only. Replace with real data.
    forecast = (1000.0, 3000.0, 5000.0, 3000.0, 1000.0, 0.0)
    settings = FBCSettings(
        capacity_kwh=10.0,
        target_soc_percent=100.0,
        charge_efficiency=0.95,
        max_charge_power_w=5000.0,
    )
    result = calculate_fbc(
        forecast,
        step_hours=1.0,
        current_soc_percent=50.0,
        current_month=10,
        settings=settings,
    )
    print("Synthetic example for manual review; not the installed battery configuration.")
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
