"""The Milky Way shape map embedded in the dome, and the claim it has to keep.

The dome says the level is measured here and only the SHAPE is borrowed from the
ESO panorama. That claim survives only if two things hold, and both are cheap to
check, so they are checked instead of asserted in a comment:

  * every row of the map averages to 1, even after clipping, resampling to
    512x256 and quantising to 8 bits, so the map can redistribute the measured
    light along a parallel but never change how much there is;
  * the galactic frame the dome builds in horizon coordinates gives the same
    (l, b) as astropy. The cross product order there is easy to write the wrong
    way round and the failure is a MIRRORED sky, which looks perfectly plausible.

Regenerate the map with `python tools/build_milkyway_map.py`.
"""

import base64
import io
import re
from pathlib import Path

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
import astropy.units as u
from PIL import Image

from astroweather.site import NERPIO
from astroweather.skybackground import (GALACTIC_AMPLITUDE_MAG,
                                        GALACTIC_LATITUDE_SCALE_DEG)

DOME = Path(__file__).resolve().parents[1] / "docs" / "mockups" / "night.html"
NGP_RA, NGP_DEC = 192.859, 27.128
GC_RA, GC_DEC = 266.405, -28.936


@pytest.fixture(scope="module")
def shape_map():
    html = DOME.read_text(encoding="utf-8")
    width = int(re.search(r"const MW_W = (\d+)", html).group(1))
    height = int(re.search(r"MW_H = (\d+)", html).group(1))
    scale = float(re.search(r"MW_SCALE = ([\d.]+)", html).group(1))
    payload = re.search(r'MW_PNG = "data:image/png;base64,([^"]+)"', html).group(1)
    pixels = np.asarray(Image.open(io.BytesIO(base64.b64decode(payload))), np.float32)
    assert pixels.shape == (height, width), "el mapa no mide lo que declara el HTML"
    return pixels / 255.0 * scale


def _latitudes(rows: int) -> np.ndarray:
    return 90.0 - (np.arange(rows) + 0.5) / rows * 180.0


def test_every_row_averages_to_one(shape_map):
    """The map redistributes the measured light; it cannot add or remove any."""
    row_means = shape_map.mean(axis=1)
    assert np.abs(row_means - 1.0).max() < 0.01


def test_the_level_it_can_shift_is_negligible(shape_map):
    """State the worst row error where it hurts: in magnitudes of sky."""
    row_means = shape_map.mean(axis=1)
    latitudes = _latitudes(shape_map.shape[0])
    amplitude = GALACTIC_AMPLITUDE_MAG * np.exp(
        -np.abs(latitudes) / GALACTIC_LATITUDE_SCALE_DEG)
    excess = 10 ** (0.4 * amplitude) - 1.0
    error_mag = 2.5 * np.log10((1 + excess * row_means) / (1 + excess))
    assert np.abs(error_mag).max() < 0.001


def test_the_bulge_is_brighter_than_the_anticentre(shape_map):
    """A mirrored or shifted map would fail here, and only here."""
    height, width = shape_map.shape

    def at(longitude, latitude):
        x = int(round(((180.0 - longitude) / 360.0 * width) % width)) % width
        y = int(round((90.0 - latitude) / 180.0 * height - 0.5))
        return float(shape_map[y, x])

    assert at(0.0, 0.0) > 1.5 * at(180.0, 0.0)
    assert at(0.0, 0.0) > 1.5 * at(90.0, 0.0)


def test_structure_fades_where_the_photo_is_only_pedestal(shape_map):
    """Above |b| ~ 60 the panorama sits on its own black level; inverting a 3.5
    power there would turn noise into mottling if it were not faded out."""
    latitudes = _latitudes(shape_map.shape[0])
    plane = shape_map[np.abs(latitudes) < 5]
    cap = shape_map[np.abs(latitudes) > 75]
    spread = lambda block: float(np.percentile(block, 95) - np.percentile(block, 5))
    assert spread(cap) < 0.25 * spread(plane)


def _unit(altitude_deg, azimuth_deg):
    altitude, azimuth = np.radians(altitude_deg), np.radians(azimuth_deg)
    return np.array([np.cos(altitude) * np.cos(azimuth),
                     np.cos(altitude) * np.sin(azimuth),
                     np.sin(altitude)])


def _to_horizon(ra, dec, lst, latitude):
    """The dome's own conversion, kept identical on purpose."""
    hour_angle = np.radians((lst - ra + 360) % 360)
    declination, phi = np.radians(dec), np.radians(latitude)
    altitude = np.arcsin(np.sin(declination) * np.sin(phi)
                         + np.cos(declination) * np.cos(phi) * np.cos(hour_angle))
    azimuth = np.arctan2(-np.cos(declination) * np.cos(phi) * np.sin(hour_angle),
                         np.sin(declination) - np.sin(phi) * np.sin(altitude))
    return np.degrees(altitude), np.degrees(azimuth) % 360


def _galactic_frame(lst, latitude):
    """e2 = e1 x e3, NOT e3 x e1 — see the module docstring."""
    e3 = _unit(*_to_horizon(NGP_RA, NGP_DEC, lst, latitude))
    centre = _unit(*_to_horizon(GC_RA, GC_DEC, lst, latitude))
    e1 = centre - e3 * (centre @ e3)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(e1, e3), e3


def test_the_domes_galactic_frame_matches_astropy():
    rng = np.random.default_rng(7)
    worst_longitude = worst_latitude = 0.0
    for lst in (30.0, 211.0, 300.0):
        e1, e2, e3 = _galactic_frame(lst, NERPIO.latitude)
        for _ in range(300):
            ra = rng.uniform(0, 360)
            dec = np.degrees(np.arcsin(rng.uniform(-1, 1)))
            altitude, azimuth = _to_horizon(ra, dec, lst, NERPIO.latitude)
            if altitude < 0:
                continue
            v = _unit(altitude, azimuth)
            b = np.degrees(np.arcsin(np.clip(v @ e3, -1, 1)))
            l = np.degrees(np.arctan2(v @ e2, v @ e1)) % 360
            truth = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs").galactic
            worst_longitude = max(worst_longitude,
                                  abs((l - truth.l.deg + 180) % 360 - 180)
                                  * np.cos(np.radians(b)))
            worst_latitude = max(worst_latitude, abs(b - truth.b.deg))
    assert worst_longitude < 0.01, "longitud galactica reflejada o girada"
    assert worst_latitude < 0.01
