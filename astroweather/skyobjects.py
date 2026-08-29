"""The sky you would actually recognise: Milky Way, bright stars, planets.

A forecast chart nobody can orient themselves on is a chart nobody reads. This
module supplies the landmarks, and it draws a hard line about what they are:

* The MILKY WAY is not decoration here. Its brightness comes from the galactic
  term measured at this site (skybackground.galactic_excess), so the band on
  the chart is as bright as the archive says it is on the night in question.
* The STARS are for orientation only. Positions are hand-entered J2000 and
  good to a fraction of a degree, which is right for recognising Cassiopeia
  and wrong for anything photometric. They are never fed to the physics.
* The PLANETS have real ephemerides but NOMINAL magnitudes: a proper planetary
  magnitude wants phase angle, ring tilt and opposition surge, and none of
  that changes whether tonight is worth driving out for.

Everything is published in equatorial coordinates plus the local sidereal time
of each frame, so the renderer does one cheap rotation instead of the payload
carrying every position at every step.
"""

from __future__ import annotations

import datetime as dt
import math

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord, get_body
from astropy.time import Time

from .ephem import location
from .site import NERPIO, Site
from .skybackground import GALACTIC_AMPLITUDE_MAG, galactic_excess

# Bright stars, J2000, for orientation. The magnitude drives the dot size and
# nothing else. Southern stars that never clear this site's horizon are left
# out rather than drawn below the ridge.
BRIGHT_STARS: list[tuple[str, float, float, float]] = [
    ("Sirio", 101.287, -16.716, -1.46),
    ("Arturo", 213.915, 19.182, -0.05),
    ("Vega", 279.234, 38.784, 0.03),
    ("Capella", 79.172, 45.998, 0.08),
    ("Rigel", 78.634, -8.202, 0.13),
    ("Procyon", 114.825, 5.225, 0.34),
    ("Betelgeuse", 88.793, 7.407, 0.50),
    ("Altair", 297.696, 8.868, 0.76),
    ("Aldebaran", 68.980, 16.509, 0.85),
    ("Spica", 201.298, -11.161, 0.97),
    ("Antares", 247.352, -26.432, 1.09),
    ("Pollux", 116.329, 28.026, 1.14),
    ("Fomalhaut", 344.413, -29.622, 1.16),
    ("Deneb", 310.358, 45.280, 1.25),
    ("Regulus", 152.093, 11.967, 1.36),
    ("Castor", 113.650, 31.888, 1.58),
    ("Shaula", 263.402, -37.104, 1.62),
    ("Bellatrix", 81.283, 6.350, 1.64),
    ("Elnath", 81.573, 28.608, 1.65),
    ("Alnilam", 84.053, -1.202, 1.69),
    ("Alioth", 193.507, 55.960, 1.77),
    ("Alnitak", 85.190, -1.943, 1.77),
    ("Mirfak", 51.081, 49.861, 1.79),
    ("Dubhe", 165.932, 61.751, 1.79),
    ("Kaus Australis", 276.043, -34.385, 1.85),
    ("Alkaid", 206.885, 49.313, 1.86),
    ("Menkalinan", 89.882, 44.947, 1.90),
    ("Alhena", 99.428, 16.399, 1.93),
    ("Polar", 37.955, 89.264, 1.98),
    ("Alphard", 141.897, -8.659, 1.98),
    ("Hamal", 31.793, 23.462, 2.00),
    ("Diphda", 10.897, -17.987, 2.04),
    ("Nunki", 283.816, -26.297, 2.05),
    ("Alpheratz", 2.097, 29.090, 2.06),
    ("Mirach", 17.433, 35.621, 2.06),
    ("Rasalhague", 263.734, 12.560, 2.08),
    ("Saiph", 86.939, -9.670, 2.09),
    ("Almach", 30.975, 42.330, 2.10),
    ("Denebola", 177.265, 14.572, 2.11),
    ("Algol", 47.042, 40.956, 2.12),
    ("Alphecca", 233.672, 26.715, 2.23),
    ("Sadr", 305.557, 40.257, 2.23),
    ("Mizar", 200.981, 54.925, 2.23),
    ("Mintaka", 83.002, -0.299, 2.23),
    ("Etamin", 269.152, 51.489, 2.23),
    ("Schedar", 10.127, 56.537, 2.24),
    ("Caph", 2.294, 59.150, 2.28),
    ("Merak", 165.460, 56.382, 2.37),
    ("Enif", 326.046, 9.875, 2.39),
    ("Scheat", 345.944, 28.083, 2.42),
    ("Ruchbah", 21.454, 60.235, 2.68),
    ("Albireo", 292.680, 27.960, 3.05),
]

# Nominal magnitudes, flagged as such wherever they are shown.
PLANETS: dict[str, float] = {
    "Venus": -4.0, "Júpiter": -2.2, "Marte": 0.5, "Saturno": 0.6, "Mercurio": 0.0,
}


# Where the galaxy's light actually piles up, in galactic longitude. The
# LEVEL of the band is ours -- 0.39 mag at the plane, measured here over 50,412
# samples -- but this SHAPE is not: it is the known morphology of the Milky Way
# (the Sagittarius bulge, the Cygnus star clouds, the Great Rift of dust, the
# dim anticentre). Said plainly because the two have very different standing.
def galaxy_structure(longitude_deg, latitude_deg):
    """Relative brightness along the band, mean normalised to about one."""
    import numpy as _np
    l = _np.asarray(longitude_deg, dtype=float) % 360.0
    b = _np.asarray(latitude_deg, dtype=float)
    delta = lambda centre: _np.abs((l - centre + 180.0) % 360.0 - 180.0)
    value = (1.0
             + 1.15 * _np.exp(-(delta(0) / 30.0) ** 2)      # Sagittarius bulge
                    * _np.exp(-(b / 14.0) ** 2)
             + 0.60 * _np.exp(-(delta(80) / 20.0) ** 2)     # Cygnus star clouds
             + 0.30 * _np.exp(-(delta(35) / 24.0) ** 2)     # Scutum-Aquila
             - 0.45 * _np.exp(-(delta(180) / 55.0) ** 2)    # dim anticentre
             # The Great Rift: dust in front of the bulge, not absence of stars.
             - 0.55 * _np.exp(-((b - 3.5) / 4.5) ** 2)
                    * _np.exp(-(delta(40) / 48.0) ** 2))
    return _np.clip(value, 0.15, None) / 1.18


def milky_way_field(count: int = 2600, seed: int = 20260827) -> list[list]:
    """A synthetic star field standing in for the galaxy's integrated light.

    Drawing the Milky Way as a smooth exponential blob is honest about the
    photometry and useless to look at, because the real thing is resolved into
    star clouds. So the diffuse term keeps the measured brightness and this
    supplies the texture: stars scattered with the density of the band, which
    is what the Milky Way physically IS.

    Deterministic by seed, so the same sky comes back every time.
    """
    rng = np.random.default_rng(seed)
    longitudes, latitudes, weights = [], [], []
    while len(longitudes) < count:
        batch = count
        l = rng.uniform(0, 360, batch)
        # Star clouds are tighter than the photometric scale height: the band
        # you can SEE is narrower than the light it contributes.
        b = rng.laplace(0.0, 8.5, batch)
        keep = np.abs(b) < 45
        l, b = l[keep], b[keep]
        density = galaxy_structure(l, b)
        accepted = rng.uniform(0, density.max(), len(l)) < density
        longitudes.extend(l[accepted]); latitudes.extend(b[accepted])
        weights.extend(density[accepted] / density.max())
    longitudes = np.array(longitudes[:count]); latitudes = np.array(latitudes[:count])
    weights = np.array(weights[:count])
    coordinates = SkyCoord(l=longitudes * u.deg, b=latitudes * u.deg,
                           frame="galactic").icrs
    return [[round(float(r), 2), round(float(d), 2), round(float(w), 2)]
            for r, d, w in zip(coordinates.ra.deg, coordinates.dec.deg, weights)]


def milky_way_bands(longitude_step: int = 4) -> list[dict]:
    """The galaxy as a stack of bands, each carrying its measured brightness.

    Drawn as several strips of constant galactic latitude rather than a single
    line, because that is what the measurement describes: an exponential decay
    away from the plane with a 26.5 degree scale, not an edge.
    """
    longitudes = np.arange(0.0, 360.0 + longitude_step, longitude_step)
    bands = []
    for latitude in (0.0, 10.0, -10.0, 20.0, -20.0, 32.0, -32.0):
        coordinates = SkyCoord(l=longitudes * u.deg, b=latitude * u.deg,
                               frame="galactic").icrs
        excess = galactic_excess(latitude)
        bands.append({
            "b": latitude,
            # Relative to the peak, so the renderer paints the measured profile.
            "weight": round(excess / GALACTIC_AMPLITUDE_MAG, 3),
            "excess_mag": round(excess, 3),
            "points": [[round(float(r), 2), round(float(d), 2)]
                       for r, d in zip(coordinates.ra.deg, coordinates.dec.deg)],
        })
    return bands


def visible_stars(site: Site = NERPIO, *, magnitude_limit: float = 3.2) -> list[dict]:
    """Bright stars that actually clear this site's horizon at some point."""
    stars = []
    for name, ra, dec, magnitude in BRIGHT_STARS:
        if magnitude > magnitude_limit:
            continue
        if dec < site.latitude - 90.0 + 8.0:     # never usefully above the ridge
            continue
        stars.append({"name": name, "ra": ra, "dec": dec, "mag": magnitude})
    return stars


def planet_positions(when: dt.datetime, site: Site = NERPIO) -> list[dict]:
    """Where the naked-eye planets sit, with nominal brightness.

    One position for the whole night: over a single night a planet moves far
    less than the width of the dot that represents it.
    """
    time = Time(when.astimezone(dt.timezone.utc))
    where = location(site)
    results = []
    for name, magnitude in PLANETS.items():
        body = {"Venus": "venus", "Júpiter": "jupiter", "Marte": "mars",
                "Saturno": "saturn", "Mercurio": "mercury"}[name]
        position = get_body(body, time, where).icrs
        results.append({"name": name, "ra": round(float(position.ra.deg), 3),
                        "dec": round(float(position.dec.deg), 3),
                        "mag": magnitude, "magnitude_is_nominal": True})
    return results


def sidereal_degrees(times: list[dt.datetime], site: Site = NERPIO) -> list[float]:
    """Local apparent sidereal time, in degrees, one per frame.

    This is the only time-dependent quantity the renderer needs to place every
    fixed object: hour angle = LST - right ascension, and the rest is geometry.
    """
    when = Time([t.astimezone(dt.timezone.utc) for t in times])
    lst = when.sidereal_time("apparent", longitude=site.longitude * u.deg)
    return [round(float(v), 4) for v in lst.deg]


def to_horizon(ra_deg: float, dec_deg: float, lst_deg: float,
               latitude_deg: float) -> tuple[float, float]:
    """Equatorial to (altitude, azimuth), the same rotation the renderer does.

    Kept here in Python so the JavaScript version can be checked against it
    rather than trusted.
    """
    hour_angle = math.radians((lst_deg - ra_deg + 360.0) % 360.0)
    dec = math.radians(dec_deg)
    lat = math.radians(latitude_deg)
    altitude = math.asin(math.sin(dec) * math.sin(lat)
                         + math.cos(dec) * math.cos(lat) * math.cos(hour_angle))
    azimuth = math.atan2(-math.cos(dec) * math.cos(lat) * math.sin(hour_angle),
                         math.sin(dec) - math.sin(lat) * math.sin(altitude))
    return math.degrees(altitude), math.degrees(azimuth) % 360.0
