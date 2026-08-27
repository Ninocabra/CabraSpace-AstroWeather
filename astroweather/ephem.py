"""Sun and Moon geometry for the observing night (SPEC v8 sections 0 and 2.2).

Everything the product shows lives inside one window: sunset-1h to sunrise+1h.
This module defines that window and the Moon's path through it, from precise
ephemerides rather than the closed-form approximations amateur tools often use
-- the whole recommendation engine hangs off these angles.

No network, no provider, no expiry: this is the one layer that can never
degrade or go stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, get_body, get_sun
from astropy.time import Time

from .site import NERPIO, Site

# Sun altitudes that delimit twilight, in degrees.
CIVIL, NAUTICAL, ASTRONOMICAL = -6.0, -12.0, -18.0
# The product window reaches an hour beyond sunset and sunrise.
WINDOW_MARGIN = timedelta(hours=1)


def location(site: Site = NERPIO) -> EarthLocation:
    """The observatory as astropy sees it, at its real elevation."""
    return EarthLocation(lat=site.latitude * u.deg, lon=site.longitude * u.deg,
                         height=site.site_elevation * u.m)


@dataclass(frozen=True)
class SkyPosition:
    t_utc: datetime
    altitude: float          # degrees above the horizon, negative below
    azimuth: float           # degrees east of north

    @property
    def airmass(self) -> float | None:
        """Kasten-Young airmass; None below the horizon, where it has no meaning."""
        if self.altitude <= 0:
            return None
        zenith = 90.0 - self.altitude
        return 1.0 / (np.cos(np.radians(zenith))
                      + 0.50572 * (96.07995 - zenith) ** -1.6364)


def _positions(body: str, times: list[datetime], site: Site):
    when = Time([t.astimezone(timezone.utc) for t in times])
    frame = AltAz(obstime=when, location=location(site))
    target = get_sun(when) if body == "sun" else get_body(body, when)
    altaz = target.transform_to(frame)
    return altaz.alt.deg, altaz.az.deg


def sun_positions(times: list[datetime], site: Site = NERPIO) -> list[SkyPosition]:
    altitudes, azimuths = _positions("sun", times, site)
    return [SkyPosition(t, float(alt), float(az))
            for t, alt, az in zip(times, altitudes, azimuths)]


def moon_positions(times: list[datetime], site: Site = NERPIO) -> list[SkyPosition]:
    altitudes, azimuths = _positions("moon", times, site)
    return [SkyPosition(t, float(alt), float(az))
            for t, alt, az in zip(times, altitudes, azimuths)]


def moon_elongation(when: datetime) -> float:
    """Sun-Moon angular separation in degrees: 180 is full Moon, 0 is new.

    Computed geocentrically, which is how the phase is defined; the topocentric
    parallax is under a degree and would only add a transformation warning.
    """
    time = Time(when.astimezone(timezone.utc))
    return float(get_body("moon", time).separation(get_sun(time)).deg)


def moon_phase_angle(when: datetime) -> float:
    """Phase angle alpha in degrees, as Krisciunas & Schaefer define it.

    ZERO IS FULL MOON. This is not the elongation (180 at full) and not the
    illuminated fraction: feeding the wrong one to the moonlight model inverts
    every night of the year, and it does it quietly.
    """
    return 180.0 - moon_elongation(when)


def moon_illumination(when: datetime) -> float:
    """Illuminated fraction of the disc, 0 to 1 -- for display, not for K&S."""
    return float((1.0 + np.cos(np.radians(moon_phase_angle(when)))) / 2.0)


def _crossings(times: list[datetime], altitudes, threshold: float):
    """Times at which an altitude curve crosses a threshold, by interpolation."""
    down, up = None, None
    for index in range(1, len(altitudes)):
        before, after = altitudes[index - 1], altitudes[index]
        if (before - threshold) * (after - threshold) >= 0:
            continue
        span = (times[index] - times[index - 1]).total_seconds()
        fraction = (threshold - before) / (after - before)
        moment = times[index - 1] + timedelta(seconds=span * fraction)
        if after < before:
            down = down or moment
        else:
            up = up or moment
    return down, up


@dataclass(frozen=True)
class Night:
    """One observing night, framed the way the product talks about it."""

    night: datetime            # the date the night began
    sunset: datetime | None
    sunrise: datetime | None
    dusk_astronomical: datetime | None
    dawn_astronomical: datetime | None
    window_start: datetime | None
    window_end: datetime | None

    @property
    def dark_hours(self) -> float:
        """Hours of true astronomical darkness; zero during summer twilight."""
        if not (self.dusk_astronomical and self.dawn_astronomical):
            return 0.0
        return max(0.0, (self.dawn_astronomical
                         - self.dusk_astronomical).total_seconds() / 3600)

    def hours(self, step_minutes: int = 10) -> list[datetime]:
        """The sampling grid the rest of the pipeline works on."""
        if not (self.window_start and self.window_end):
            return []
        grid, cursor = [], self.window_start
        while cursor <= self.window_end:
            grid.append(cursor)
            cursor += timedelta(minutes=step_minutes)
        return grid


def night_for(day: datetime, site: Site = NERPIO, step_minutes: int = 5) -> Night:
    """Frame the night that BEGINS on the given day.

    A night spans two calendar dates, so it is labelled by the date it started;
    the whole archive follows the same convention.
    """
    start = day.astimezone(timezone.utc).replace(hour=6, minute=0, second=0,
                                                 microsecond=0)
    times = [start + timedelta(minutes=step_minutes * i)
             for i in range(int(36 * 60 / step_minutes))]
    altitudes, _ = _positions("sun", times, site)
    altitudes = list(map(float, altitudes))

    sunset, sunrise = _crossings(times, altitudes, 0.0)
    dusk, dawn = _crossings(times, altitudes, ASTRONOMICAL)

    return Night(
        night=start,
        sunset=sunset,
        sunrise=sunrise,
        dusk_astronomical=dusk,
        dawn_astronomical=dawn,
        window_start=sunset - WINDOW_MARGIN if sunset else None,
        window_end=sunrise + WINDOW_MARGIN if sunrise else None,
    )
