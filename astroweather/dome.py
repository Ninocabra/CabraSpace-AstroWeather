"""Everything the sky dome needs, projected to (altitude, azimuth).

The forecast is a set of numbers at a point; a first-person view needs to know
WHERE in the sky each of them belongs. Three projections do that work:

* The Moon and the targets already have directions: ephemerides give them.
* Moonlight has one too, because Krisciunas & Schaefer is a function of the
  angular separation from the Moon -- the glow dome is physics, not decoration.
* Clouds do NOT, from a single point. They get one from a GRID of forecast
  points around the site plus the typical height of each layer: a cell 45 km
  away in a given azimuth, with its cloud base at 9 km, sits at 11 degrees of
  elevation in that azimuth. Simple trigonometry, and honest.

The last one is what makes this different from a flat forecast, and also what
sets its limit: the model resolves 9-25 km, so what is being drawn is the
structure of the MODEL. The dome therefore renders cloud as diffuse patches
whose sharpness follows the ensemble agreement, never as crisp objects.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, asdict

import requests

from .ephem import (ASTRONOMICAL, moon_illumination, moon_phase_angle, moon_positions,
                    night_for, sun_positions)
from .extinction import evaluate as evaluate_transparency
from .moonsky import moon_brightness, nl_to_mag, mag_to_nl
from .seeing import integrate
from .site import NERPIO, Site
from .queries import night_inputs, night_series
from .skymodel import predict
from .targets import CATALOGUE, evaluate_night

# Typical height above the site of each cloud layer, in metres. Low cloud only
# matters when it is close; high cloud is visible from a hundred kilometres.
LAYER_HEIGHT_M = {"low": 1500.0, "mid": 5000.0, "high": 9000.0}
# The ladder has to reach in CLOSE, not just far. A cell 20 km away carries
# low cloud at 4 degrees of elevation, so a ring set that starts at 20 km can
# only ever paint low cloud onto the horizon -- and the cloud that decides a
# night is the one directly overhead. Distances near zero are what fill the
# top of the dome; distances far out are what fill the rim.
GRID_RINGS_KM = (2, 5, 10, 20, 45, 90)
GRID_AZIMUTHS = tuple(range(0, 360, 45))
# Above this elevation a patch is treated as overhead: azimuth stops meaning
# anything near the zenith, so it is drawn as a cap rather than a direction.
ZENITH_CAP_DEG = 78.0


def grid_points(site: Site = NERPIO):
    """The site plus rings of forecast points around it."""
    points = [(site.latitude, site.longitude, 0.0, 0.0)]
    for distance in GRID_RINGS_KM:
        for azimuth in GRID_AZIMUTHS:
            d_lat = distance / 111.0 * math.cos(math.radians(azimuth))
            d_lon = (distance / (111.0 * math.cos(math.radians(site.latitude)))
                     * math.sin(math.radians(azimuth)))
            points.append((site.latitude + d_lat, site.longitude + d_lon,
                           float(distance), float(azimuth)))
    return points


def fetch_cloud_grid(site: Site = NERPIO, days: int = 2) -> tuple[list, list]:
    """One call, many coordinates: the directional cloud field."""
    points = grid_points(site)
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": ",".join(f"{p[0]:.4f}" for p in points),
            "longitude": ",".join(f"{p[1]:.4f}" for p in points),
            "hourly": ("cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
                       "relative_humidity_2m,dew_point_2m,temperature_2m,wind_speed_10m"),
            "models": "ecmwf_ifs", "forecast_days": days, "timezone": "UTC",
        },
        timeout=90,
    )
    response.raise_for_status()
    return points, response.json()


def project_clouds(points, payload, index: int) -> list[dict]:
    """Turn the grid into patches on the dome for one time step.

    The site's own cell used to be dropped here on the grounds that a cell
    directly overhead has no azimuth. True, and irrelevant: it has a POSITION,
    the zenith, and dropping it left the top of the dome permanently clear no
    matter what the forecast said.
    """
    patches = []
    for point, cell in zip(points, payload):
        _, _, distance, azimuth = point
        hourly = cell["hourly"]
        for layer, key in (("high", "cloud_cover_high"),
                           ("mid", "cloud_cover_mid"),
                           ("low", "cloud_cover_low")):
            series = hourly.get(key) or []
            cover = series[index] if index < len(series) else None
            if not cover or cover < 5:
                continue
            if distance == 0:
                elevation, span = 90.0, 60.0      # straight up, and wide
            else:
                elevation = math.degrees(math.atan2(LAYER_HEIGHT_M[layer],
                                                    distance * 1000.0))
                # Angular size falls with distance: near cloud covers more sky.
                span = min(60.0, math.degrees(
                    math.atan2(15000.0, distance * 1000.0)))
            patches.append({
                "az": azimuth, "alt": round(elevation, 1),
                "cover": cover, "layer": layer,
                "span": round(span, 1),
                "distance_km": distance,
                "overhead": elevation >= ZENITH_CAP_DEG,
            })
    return patches


def moon_glow_profile(phase_angle: float, moon_alt: float, k: float,
                      separations=(5, 15, 30, 50, 80, 120, 170)) -> list[dict]:
    """How the Moon's glow falls off with angular distance.

    This is the part of the render that is pure physics: the dome is not
    painting a decorative halo, it is painting the scattering function.

    WHICH scattering function DEPENDS ON THE SEPARATION, and saying "from K&S"
    as a blanket label -- which this docstring did until 2026-09-01 -- gets it
    wrong in BOTH directions. `moonscatter.scattering` crosses over at about 84
    degrees: below that the site's own fitted exponential wins, above it the
    published Mauna Kea function takes over as a floor, so that an exponential
    measured out to 80 degrees is never extrapolated to 170. Of the separations
    sampled here, five (5 to 80) are the SITE fit and two (120, 170) are the
    imported floor.

    The distinction earns its lines because a constant provenance label is
    believed: it is how you end up claiming a local calibration you are not
    using, or crediting a published law for a number you fitted yourself.
    Learned from the CabraTransit session, which had the mirror-image bug and
    found it the same day.
    """
    profile = []
    for separation in separations:
        brightness = moon_brightness(
            phase_angle_deg=phase_angle, separation_deg=float(separation),
            moon_altitude_deg=moon_alt, target_altitude_deg=60.0,
            extinction_k=k)
        profile.append({"sep": separation,
                        "mag": round(brightness.physical_mag, 2)
                        if brightness.physical_nl > 0 else None})
    return profile


def build(day: dt.datetime | None = None, site: Site = NERPIO,
          *, aod_total: float | None = None, aod_dust: float | None = None,
          jet_speed: float | None = None, with_clouds: bool = True) -> dict:
    """The whole night, ready for the renderer.

    Aerosol and jet speed default to the FORECAST for this night, not to a
    constant. They were constants until 2026-08-28, and the forecast for both
    had been archived and unread for months.
    """
    day = day or dt.datetime.now(dt.timezone.utc)
    night = night_for(day, site)
    times = night.hours(30)
    forecast = night_inputs(times[0], times[-1], site.site_id)
    aod_total = forecast["aod_total"] if aod_total is None else aod_total
    aod_dust = forecast["aod_dust"] if aod_dust is None else aod_dust
    jet_speed = forecast["jet_speed"] if jet_speed is None else jet_speed

    skies = predict(times, site)
    moons = moon_positions(times, site)
    suns = sun_positions(times, site)
    phase = moon_phase_angle(times[len(times) // 2])
    transparency = evaluate_transparency(
        aod_total=aod_total, aod_dust=aod_dust,
        elevation_m=site.site_elevation, delta_h_m=site.delta_h)
    turbulence = integrate(jet_speed)

    points, payload = ([], [])
    if with_clouds:
        try:
            points, payload = fetch_cloud_grid(site)
        except Exception:
            points, payload = [], []

    # Line up the grid's hourly index with our half-hourly grid.
    grid_times = []
    if payload:
        grid_times = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
                      for t in payload[0]["hourly"]["time"]]

    frames = []
    for index, when in enumerate(times):
        cloud_index = min(range(len(grid_times)),
                          key=lambda i: abs((grid_times[i] - when).total_seconds())
                          ) if grid_times else None
        frames.append({
            "t": when.isoformat(),
            "label": when.strftime("%H:%M"),
            "sun_alt": round(suns[index].altitude, 1),
            "moon_alt": round(moons[index].altitude, 1),
            "moon_az": round(moons[index].azimuth, 1),
            "sky_mag": round(skies[index].predicted_mag, 2),
            "clouds": (project_clouds(points, payload, cloud_index)
                       if cloud_index is not None else []),
        })

    # M6 (26-10-2026): E[Q] solo cuenta la noche astronomica (Sol < -18 grados),
    # como el veredicto y `nightview`.
    targets = evaluate_night(CATALOGUE, times,
                             usable_probability=[0.8 if s.altitude < ASTRONOMICAL
                                                 else 0.0 for s in suns],
                             turbulence=turbulence, aod_total=aod_total,
                             aod_dust=aod_dust, site=site,
                             serie=night_series(times[0], times[-1], site.site_id))
    tracks = []
    for result in targets[:6]:
        altitudes, azimuths, separations = result.target.positions(times, site)
        tracks.append({
            "name": result.target.name,
            "difficulty": result.target.difficulty,
            "q_cond": round(result.q_conditional, 1),
            "q_expected": round(result.q_expected, 1),
            "reason": result.reason,
            "path": [{"alt": round(float(a), 1), "az": round(float(z), 1)}
                     for a, z in zip(altitudes, azimuths)],
            "moon_sep": round(result.min_moon_separation, 1),
        })

    return {
        "site": {"name": site.name, "lat": site.latitude, "lon": site.longitude,
                 "elevation": site.site_elevation},
        "night": night.night.date().isoformat(),
        "dark_hours": round(night.dark_hours, 1),
        "moon": {"illumination": round(moon_illumination(
                     night.dusk_astronomical or night.night), 3),
                 "phase_angle": round(phase, 1),
                 "glow": moon_glow_profile(phase, 45.0, transparency.k_total)},
        "transparency": {"k": round(transparency.k_total, 3),
                         "aod_site": round(transparency.aod_site, 3),
                         # LAS DOS JUNTAS O NINGUNA: un `dust_alert`
                         # a solas no distingue "no hay calima" de "no
                         # lo hemos mirado". El porque entero, con lo
                         # que le hace al pixel, en `nightview.build`,
                         # que es el payload que se publica.
                         "dust_alert": transparency.dust_alert,
                         "species_resolved": transparency.species_resolved,
                         "verdict": transparency.verdict,
                         "color_note": transparency.color_note},
        "seeing": {"tercile": turbulence.tercile,
                   "range": [round(turbulence.range_arcsec[0], 1),
                             round(turbulence.range_arcsec[1], 1)],
                   "jet": round(turbulence.jet_speed, 0),
                   "tau0_ms": round(turbulence.coherence_time_ms, 1),
                   "source": forecast["jet_source"],
                   "note": "estimacion experimental, sin calibrar con FWHM local"},
        "frames": frames,
        "targets": tracks,
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=1)[:2000])
