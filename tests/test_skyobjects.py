"""The landmark sky: is the hand-entered catalogue actually right?

Coordinates typed by hand are exactly the kind of thing that looks fine and is
wrong, so these check the catalogue against geometry it cannot fake.
"""

import datetime as dt

import numpy as np
import pytest
from astropy.coordinates import AltAz, SkyCoord
from astropy.time import Time
import astropy.units as u

from astroweather.ephem import location
from astroweather.site import NERPIO
from astroweather.skyobjects import (BRIGHT_STARS, milky_way_bands,
                                     planet_positions, sidereal_degrees,
                                     to_horizon, visible_stars)

STARS = {name: (ra, dec, mag) for name, ra, dec, mag in BRIGHT_STARS}
WHEN = dt.datetime(2026, 8, 27, 23, 30, tzinfo=dt.timezone.utc)


def test_orions_belt_is_collinear():
    """Three stars in a row, or the catalogue has a typo."""
    belt = [SkyCoord(STARS[n][0] * u.deg, STARS[n][1] * u.deg)
            for n in ("Mintaka", "Alnilam", "Alnitak")]
    # The middle star must sit on the line between the outer two.
    total = belt[0].separation(belt[2]).deg
    legs = belt[0].separation(belt[1]).deg + belt[1].separation(belt[2]).deg
    assert abs(legs - total) < 0.05, "the belt is bent: a coordinate is wrong"
    assert 2.0 < total < 3.5


def test_the_pole_star_is_at_the_pole():
    assert STARS["Polar"][1] > 89.0


def test_summer_triangle_has_the_right_shape():
    vega, deneb, altair = (SkyCoord(STARS[n][0] * u.deg, STARS[n][1] * u.deg)
                           for n in ("Vega", "Deneb", "Altair"))
    assert 23 < vega.separation(deneb).deg < 25
    assert 33 < vega.separation(altair).deg < 35
    assert 37 < deneb.separation(altair).deg < 39


@pytest.mark.parametrize("name", ["Vega", "Sirio", "Polar", "Antares", "Capella"])
def test_hand_entered_positions_agree_with_astropys_horizon_maths(name):
    """Our cheap rotation must match the real transform, star by star."""
    ra, dec, _ = STARS[name]
    lst = sidereal_degrees([WHEN], NERPIO)[0]
    altitude, azimuth = to_horizon(ra, dec, lst, NERPIO.latitude)

    reference = SkyCoord(ra * u.deg, dec * u.deg).transform_to(
        AltAz(obstime=Time(WHEN), location=location(NERPIO)))
    assert abs(altitude - float(reference.alt.deg)) < 0.6
    if altitude > 5.0:                      # azimuth is ill-defined at the pole
        difference = abs((azimuth - float(reference.az.deg) + 180) % 360 - 180)
        assert difference < 0.6


def test_the_milky_way_carries_the_measured_profile_not_a_flat_band():
    bands = milky_way_bands()
    by_latitude = {b["b"]: b for b in bands}
    assert by_latitude[0.0]["weight"] == 1.0
    assert by_latitude[10.0]["weight"] == by_latitude[-10.0]["weight"]
    assert by_latitude[32.0]["weight"] < by_latitude[10.0]["weight"] < 1.0
    # Every band closes on itself: it is a full circle of the sky.
    assert all(len(b["points"]) > 80 for b in bands)


def test_the_galactic_centre_lands_where_it_should():
    """l = 0, b = 0 is Sagittarius: RA about 266 deg, Dec about -29."""
    plane = next(b for b in milky_way_bands() if b["b"] == 0.0)
    ra, dec = plane["points"][0]
    assert abs(ra - 266.4) < 1.0
    assert abs(dec + 28.9) < 1.0


def test_southern_stars_that_never_rise_are_dropped():
    names = {s["name"] for s in visible_stars(NERPIO)}
    assert "Vega" in names
    assert all(STARS[n][1] > NERPIO.latitude - 90.0 for n in names)


def test_planets_are_labelled_as_nominal_brightness():
    planets = planet_positions(WHEN, NERPIO)
    assert {p["name"] for p in planets} >= {"Venus", "Jupiter", "Marte", "Saturno"}
    assert all(p["magnitude_is_nominal"] for p in planets)
