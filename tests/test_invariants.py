"""Tests for the ingestion invariants (SPEC v8 section 3.2).

Every test here encodes a mistake that really happened during the
specification work, or that an API default would have caused.
"""

from datetime import datetime, timedelta, timezone

import pytest

from astroweather.invariants import (
    InvariantError,
    check_elevation_request,
    check_eps_members,
    check_wind_units,
    data_quality,
    is_fresh,
    is_plausible_sky_mag,
)
from astroweather.site import NERPIO


def test_wind_in_kmh_is_rejected():
    # The real value that fooled us: 127.7 km/h at 250 hPa read as m/s.
    with pytest.raises(InvariantError):
        check_wind_units([127.7], label="v250")


def test_wind_in_ms_passes():
    check_wind_units([35.5, None, 8.0], label="v250")


def test_surface_request_without_elevation_is_rejected():
    with pytest.raises(InvariantError):
        check_elevation_request(
            "https://api.open-meteo.com/v1/forecast?latitude=38.16583&hourly=temperature_2m"
        )


def test_surface_request_with_elevation_passes():
    check_elevation_request(
        "https://api.open-meteo.com/v1/forecast?latitude=38.16583"
        "&elevation=1650&hourly=temperature_2m"
    )


def test_partial_ensemble_is_rejected():
    names = ["cloud_cover"] + [f"cloud_cover_member{i:02d}" for i in range(1, 40)]
    with pytest.raises(InvariantError):
        check_eps_members(names, variable="cloud_cover")


def test_full_ensemble_passes():
    names = ["cloud_cover"] + [f"cloud_cover_member{i:02d}" for i in range(1, 51)]
    check_eps_members(names, variable="cloud_cover")


def test_stale_sensor_reading_is_not_fresh():
    # cloud.html serves a weather station frozen since 2025 next to live ones.
    stale = datetime.now(timezone.utc) - timedelta(days=400)
    assert not is_fresh(stale)
    assert is_fresh(datetime.now(timezone.utc) - timedelta(minutes=3))
    assert not is_fresh(None)


def test_garbage_photometer_values_are_rejected():
    # Live TESS stations emitting mag=0 or a stuck 13.25 were found in the survey.
    assert not is_plausible_sky_mag(0.0)
    assert not is_plausible_sky_mag(13.25)
    assert is_plausible_sky_mag(21.97)


def test_data_quality_declares_what_is_missing():
    quality = data_quality({"clouds": True, "aod_dust": False})
    assert quality["degraded"]
    assert quality["missing"] == ["aod_dust"]


def test_site_delta_h_uses_model_grid_not_dem():
    # 1650 - 1067; the 1302/1542 m DEM figures are NOT the model topography.
    assert NERPIO.delta_h == pytest.approx(583.0)
