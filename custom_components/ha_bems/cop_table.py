"""Editable COP lookup table text and representative reference values."""

from .thermal_cost import cop_points, interpolate_cop

DEFAULT_COP_POINTS = [(-7, 2.32), (2, 3.12), (7, 4.11), (12, 4.61)]


def default_table_text(count, saved=None):
    """Keep saved points; resize without extrapolating beyond their range."""
    points = cop_points(saved) if saved else list(DEFAULT_COP_POINTS)
    if count < len(points):
        points = [points[round(i * (len(points) - 1) / (count - 1))] for i in range(count)]
    while len(points) < count:
        index = max(range(len(points) - 1), key=lambda i: points[i + 1][0] - points[i][0])
        temperature = (points[index][0] + points[index + 1][0]) / 2
        points.insert(index + 1, (temperature, interpolate_cop(temperature, points)))
    return "\n".join(f"{temperature};{cop}" for temperature, cop in points)


def parse_table_text(text, count):
    """One temperature;COP pair per line, accepting decimal dots or commas."""
    if not isinstance(text, str):
        raise ValueError("Enter the lookup table as text")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != count:
        raise ValueError("The number of rows must match the selected reference point count")
    points = []
    for line in lines:
        columns = line.split(";")
        if len(columns) != 2:
            raise ValueError("Each row must contain outdoor temperature;COP")
        temperature, cop = (float(value.strip().replace(",", ".")) for value in columns)
        points.append({"temperature_c": temperature, "cop": cop})
    return [{"temperature_c": temperature, "cop": cop} for temperature, cop in cop_points(points)]
