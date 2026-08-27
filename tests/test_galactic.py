"""The Milky Way in the sky budget, measured rather than assumed.

Added after an external audit noted that the zodiacal light was modelled and
the galaxy was not, despite being the larger of the two here.
"""

import datetime as dt

import numpy as np

from astroweather.skybackground import (GALACTIC_AMPLITUDE_MAG,
                                        ZODIACAL_AMPLITUDE_MAG, budget,
                                        galactic_excess, zenith_galactic)
from astroweather.site import NERPIO


def _a_night():
    start = dt.datetime(2026, 3, 15, 22, tzinfo=dt.timezone.utc)
    return [start + dt.timedelta(minutes=20 * i) for i in range(24)]


def test_the_galaxy_is_the_larger_term():
    """It was left out while the smaller contributor was modelled."""
    assert GALACTIC_AMPLITUDE_MAG > ZODIACAL_AMPLITUDE_MAG


def test_excess_peaks_on_the_plane_and_dies_at_the_pole():
    assert galactic_excess(0.0) == GALACTIC_AMPLITUDE_MAG
    assert galactic_excess(90.0) < 0.02
    assert galactic_excess(-30.0) == galactic_excess(30.0)     # symmetric in |b|
    latitudes = [0, 15, 30, 45, 60, 90]
    values = [galactic_excess(b) for b in latitudes]
    assert all(a > b for a, b in zip(values, values[1:]))


def test_the_zenith_of_this_site_really_does_cross_the_galactic_plane():
    """The whole measurement rests on this geometry.

    A stationary photometer can only measure the Milky Way if the zenith it
    stares at sweeps through the plane. At +38.17 deg it does, reaching |b| = 0;
    it never reaches the ecliptic, which is why the zodiacal amplitude is an
    extrapolation and this one is not.
    """
    year = [dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(hours=6 * i)
            for i in range(1460)]
    latitudes = np.abs(zenith_galactic(year, NERPIO))
    assert latitudes.min() < 2.0
    assert latitudes.max() > 70.0


def test_budget_reports_the_galaxy_and_darkens_the_sky_off_the_plane():
    entries = budget(_a_night(), NERPIO)
    assert all(e.galactic <= 0.0 for e in entries)       # it can only brighten
    on_plane = min(entries, key=lambda e: abs(e.galactic_latitude))
    off_plane = max(entries, key=lambda e: abs(e.galactic_latitude))
    if abs(on_plane.galactic_latitude) + 15 < abs(off_plane.galactic_latitude):
        assert on_plane.total_mag < off_plane.total_mag
    assert "via lactea" in entries[0].breakdown


def test_components_are_combined_in_flux_not_by_subtracting_magnitudes():
    """Three magnitude offsets stacked on the floor compound each other.

    With the galaxy in the budget there are three, so the shortcut that was
    tolerable for one is not tolerable now: it would over-brighten the sky by
    about 0.07 mag where both terms peak.
    """
    entries = budget(_a_night(), NERPIO)
    worst = min(entries, key=lambda e: e.total_mag)
    naive = worst.natural_floor + worst.zodiacal + worst.galactic + worst.airglow
    assert worst.total_mag > naive, "still stacking magnitude offsets"
    assert worst.total_mag - naive < 0.25
