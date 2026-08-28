"""Tests for the night framing and the moonlight model (SPEC v8 2.2)."""

from datetime import datetime, timezone

import pytest

from astroweather.ephem import (
    moon_elongation, moon_illumination, moon_phase_angle, night_for,
)
from astroweather.moonsky import (
    airmass_scattering, mag_to_nl, moon_brightness, nl_to_mag, sky_magnitude,
)

AUGUST = datetime(2026, 8, 27, tzinfo=timezone.utc)
JANUARY = datetime(2026, 1, 11, tzinfo=timezone.utc)


def test_the_night_is_labelled_by_the_day_it_began():
    """A night crosses midnight; the whole archive keys it by its start."""
    night = night_for(AUGUST)
    assert night.sunset.date() == AUGUST.date()
    assert night.sunrise.date() > AUGUST.date()
    assert night.window_start < night.sunset < night.sunrise < night.window_end


def test_winter_nights_are_longer_than_summer_ones():
    assert night_for(JANUARY).dark_hours > night_for(AUGUST).dark_hours


def test_the_product_window_extends_an_hour_past_twilight():
    night = night_for(AUGUST)
    assert (night.sunset - night.window_start).total_seconds() == 3600
    assert (night.window_end - night.sunrise).total_seconds() == 3600


def test_phase_angle_is_zero_at_full_moon_not_180():
    """The trap this project exists to avoid: alpha is NOT the elongation.

    Feeding the elongation to Krisciunas & Schaefer inverts every night of the
    year, quietly.
    """
    full = datetime(2026, 8, 27, 22, tzinfo=timezone.utc)
    assert moon_elongation(full) > 170        # nearly opposite the Sun
    assert moon_phase_angle(full) < 10        # ...which means alpha near zero
    assert moon_illumination(full) > 0.95


def test_new_moon_is_the_other_way_round():
    new = datetime(2026, 9, 11, 22, tzinfo=timezone.utc)
    assert moon_elongation(new) < 20
    assert moon_phase_angle(new) > 160
    assert moon_illumination(new) < 0.05


def test_scattering_airmass_is_bounded_by_design():
    """K&S bound X at 5 on the horizon; that is the model, not a bug to fix."""
    assert airmass_scattering(90) == pytest.approx(1.0)
    assert airmass_scattering(0) == pytest.approx(5.0)
    assert airmass_scattering(-10) <= 5.0


def test_full_moon_brightens_the_sky_to_the_textbook_value():
    result = moon_brightness(phase_angle_deg=0, separation_deg=90,
                             moon_altitude_deg=50, target_altitude_deg=60)
    sky = sky_magnitude([21.9, result.physical_mag])
    assert 18.0 < sky < 19.5          # the classic full-moon sky


def test_a_moon_just_below_the_horizon_still_lights_the_sky():
    """This test used to assert the opposite, and the archive said otherwise.

    The old belief was that below the horizon the physical contribution is zero
    and any roll-off is decoration. Measured here on 2026-08-28: with the Moon
    geometrically invisible the sky is still brighter, decaying with the shadow
    height of the atmosphere. That is what `beam_transmission` carries, and it
    is also the evidence that the excess is sky and not stray light in the
    instrument -- stray light needs the direct beam.
    """
    just_below = moon_brightness(phase_angle_deg=0, separation_deg=90,
                                 moon_altitude_deg=-1, target_altitude_deg=60)
    deep_below = moon_brightness(phase_angle_deg=0, separation_deg=90,
                                 moon_altitude_deg=-10, target_altitude_deg=60)
    above = moon_brightness(phase_angle_deg=0, separation_deg=90,
                            moon_altitude_deg=5, target_altitude_deg=60)
    assert just_below.physical_nl > 0.0
    assert just_below.physical_nl < above.physical_nl
    # ...and once the whole air column is in shadow, then it really is zero.
    assert deep_below.physical_nl == 0.0
    assert sky_magnitude([21.9, deep_below.physical_mag]) == pytest.approx(21.9)


def test_haze_makes_the_moon_worse_not_better():
    """More aerosol scatters more moonlight: the coupling amateurs forget."""
    clean = moon_brightness(phase_angle_deg=0, separation_deg=90,
                            moon_altitude_deg=50, target_altitude_deg=60,
                            extinction_k=0.12)
    dusty = moon_brightness(phase_angle_deg=0, separation_deg=90,
                            moon_altitude_deg=50, target_altitude_deg=60,
                            extinction_k=0.35)
    assert dusty.physical_nl > clean.physical_nl


def test_small_separations_use_the_papers_other_expression():
    """Beyond its 10-degree limit the published function is simply wrong."""
    close = moon_brightness(phase_angle_deg=0, separation_deg=4,
                            moon_altitude_deg=50, target_altitude_deg=60)
    far = moon_brightness(phase_angle_deg=0, separation_deg=90,
                          moon_altitude_deg=50, target_altitude_deg=60)
    assert close.physical_nl > far.physical_nl * 3


def test_background_components_add_in_flux_never_in_magnitudes():
    """Two equal sources are 0.75 mag brighter, not twice the number."""
    assert sky_magnitude([21.0, 21.0]) == pytest.approx(21.0 - 0.7526, abs=0.01)
    assert nl_to_mag(mag_to_nl(21.9)) == pytest.approx(21.9)
