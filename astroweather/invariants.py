"""Ingestion invariants (SPEC v8 section 3.2).

Each check here exists because the corresponding mistake actually happened, or
was one API default away from happening, during the specification work:

1. winds silently arriving in km/h (a factor 3.6 that raises no exception)
2. surface variables requested without the real site elevation
3. EPS members quietly missing (they are unrecoverable after ~4-5 days)
4. dead sensors read by table position instead of timestamp freshness
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

MAX_PLAUSIBLE_WIND_MS = 90.0   # ~324 km/h: above this the unit is wrong
EPS_MEMBER_COUNT = 51
SENSOR_MAX_AGE = timedelta(minutes=15)
TESS_MAG_RANGE = (14.0, 23.0)  # same cut the official STARS4ALL panels use


class InvariantError(ValueError):
    """An ingestion invariant was violated: refuse the data, do not guess."""


def check_wind_units(values: Iterable[float | None], *, label: str = "wind") -> None:
    for value in values:
        if value is not None and value > MAX_PLAUSIBLE_WIND_MS:
            raise InvariantError(
                f"{label}={value} exceeds {MAX_PLAUSIBLE_WIND_MS} m/s; "
                "the response is almost certainly km/h "
                "(add &wind_speed_unit=ms)"
            )


def check_elevation_request(url: str) -> None:
    """Surface variables must be downscaled to the real site elevation."""
    surface_markers = ("temperature_2m", "dew_point_2m", "surface_pressure")
    if any(marker in url for marker in surface_markers) and "elevation=" not in url:
        raise InvariantError(
            "surface request without &elevation=<site_elevation>: values would "
            "describe the provider DEM, not the observatory"
        )


def check_eps_members(series_names: Iterable[str], *, variable: str) -> None:
    members = {name for name in series_names if f"{variable}_member" in name}
    # control run + 50 perturbed members
    if len(members) + 1 != EPS_MEMBER_COUNT:
        raise InvariantError(
            f"expected {EPS_MEMBER_COUNT} EPS series for {variable}, "
            f"got {len(members) + 1}; do not archive a partial ensemble"
        )


def is_fresh(timestamp: datetime | None, *, max_age: timedelta = SENSOR_MAX_AGE) -> bool:
    """Sensor readings are selected by freshness, never by table position.

    cloud.html mixes live sensors with ones frozen since 2025.
    """
    if timestamp is None:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timestamp <= max_age


def is_plausible_sky_mag(value: float | None) -> bool:
    """Live photometers can still emit garbage (mag=0, or a stuck 13.25)."""
    if value is None:
        return False
    low, high = TESS_MAG_RANGE
    return low <= value <= high


def data_quality(present: dict[str, bool]) -> dict[str, Any]:
    """Declare what is missing instead of inventing it (graceful degradation)."""
    missing = sorted(name for name, ok in present.items() if not ok)
    return {
        "complete": not missing,
        "missing": missing,
        "degraded": bool(missing),
    }
