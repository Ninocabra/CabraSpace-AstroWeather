"""Tests for the aerosol correction (SPEC v8 section 2.1)."""

import math

import pytest

from astroweather.site import NERPIO
from astroweather.workers.fetch_aerosol import AEROSOL_SCALE_HEIGHT_M, site_aod


def test_dust_is_not_scaled_down_by_altitude():
    """Saharan dust rides at 1.5-5 km: at 1650 m you are inside it, not above.

    Scaling the whole column would report a clean sky in the middle of an
    episode -- the exact failure this correction exists to prevent.
    """
    corrected = site_aod(aod_total=0.40, aod_dust=0.30, delta_h=NERPIO.delta_h)
    assert corrected > 0.30            # the dust term survives intact
    assert corrected < 0.40            # only the boundary-layer part is reduced


def test_non_dust_aerosol_is_reduced_by_the_site_height():
    """Sulphates and sea salt do concentrate below the observatory."""
    corrected = site_aod(aod_total=0.20, aod_dust=0.0, delta_h=NERPIO.delta_h)
    expected = 0.20 * math.exp(-NERPIO.delta_h / AEROSOL_SCALE_HEIGHT_M)
    assert corrected == pytest.approx(expected, rel=1e-6)


def test_a_clean_column_stays_clean():
    assert site_aod(0.0, 0.0, NERPIO.delta_h) == 0.0


def test_missing_species_degrades_instead_of_guessing():
    """Without the split we scale everything and end up pessimistic, not blind."""
    corrected = site_aod(aod_total=0.40, aod_dust=None, delta_h=NERPIO.delta_h)
    assert 0 < corrected < 0.40


def test_missing_total_returns_nothing():
    assert site_aod(None, 0.1, NERPIO.delta_h) is None


def test_dust_never_exceeds_the_total():
    """A noisy split must not manufacture optical depth out of nowhere."""
    corrected = site_aod(aod_total=0.10, aod_dust=0.15, delta_h=NERPIO.delta_h)
    assert corrected == 0.15           # dust term kept, remainder clamped at 0
