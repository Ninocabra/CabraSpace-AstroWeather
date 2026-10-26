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


def dark_slot_weights(starts: list[datetime], step_hours: float,
                      dusk: datetime | None, dawn: datetime | None) -> list[float]:
    """Cuanto de cada casilla [t, t + paso) cuenta como noche astronomica.

    M6 (26-10-2026). La rejilla de presentacion va de ocaso-1 h a orto+1 h
    (`WINDOW_MARGIN`), pero la curva del techo (roofcalibration.py) se ajusta
    SOLO con horas cuyo punto medio tiene el Sol por debajo de
    `DARK_SUN_ALTITUDE` (= `ASTRONOMICAL`, -18 grados): aplicarla al crepusculo
    es extrapolar. Medido con el archivo: con M1 la noche del 26-10 daba 11,9
    horas esperadas con 10,3 h de noche astronomica, y 13 + 26 ventanas
    publicadas acababan despues del orto.

    Una casilla CUENTA (peso > 0) si su punto medio cae dentro de [dusk, dawn],
    que es lo mismo que Sol < -18 grados a la media hora (`dusk` y `dawn` son
    los cruces de ese mismo umbral, `night_for`). Su peso es la fraccion de la
    casilla que de verdad esta dentro de la noche astronomica: la hora que
    cruza -18 grados cuenta en proporcion, no entera. Asi la suma de
    `peso * p` nunca pasa de las horas de noche astronomica (p <= 1 y los
    solapes suman a lo sumo `dawn - dusk`); contarla entera con el criterio del
    punto medio SI podia pasarse (hasta 1 h por encima: 11 casillas en 10,3 h).

    Sin ocaso/orto astronomicos (no ocurre a 38 N, donde el Sol baja de -28 en
    el solsticio de verano) no se puede decir que es noche: todo cuenta entero,
    el comportamiento anterior, en vez de dejar la noche vacia.
    """
    if dusk is None or dawn is None:
        return [1.0] * len(starts)
    step = timedelta(hours=step_hours)
    weights = []
    for start in starts:
        if not (dusk <= start + step / 2 <= dawn):
            weights.append(0.0)
            continue
        overlap = (min(start + step, dawn) - max(start, dusk)).total_seconds()
        weights.append(max(0.0, overlap) / step.total_seconds())
    return weights


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

    def adaptive_hours(self, dark_step: int = 10, twilight_step: int = 30,
                       site: Site = NERPIO) -> list[datetime]:
        """Fine sampling where it matters, coarse where it does not.

        Ten minutes through astronomical darkness, because that is the part
        being planned against and a cloud front crosses the dome in less than
        half an hour. Thirty either side, because nobody images in twilight and
        a uniform ten-minute grid would triple the payload to say nothing.
        """
        if not (self.window_start and self.window_end):
            return []
        probe = self.hours(dark_step)
        altitudes = [p.altitude for p in sun_positions(probe, site)]
        grid, last = [], None
        for when, altitude in zip(probe, altitudes):
            step = dark_step if altitude < -18.0 else twilight_step
            if last is None or (when - last).total_seconds() >= step * 60 - 1:
                grid.append(when)
                last = when
        if grid and grid[-1] != probe[-1]:
            grid.append(probe[-1])
        return grid

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
    # Anchored at midday, not at 06:00. The old anchor assumed the Sun was
    # already up when the scan began, which is true in summer and FALSE from
    # October to March at this latitude: at 06:00 UTC in January it is still
    # night here, so the first crossing found was a sunrise, the window came
    # out inverted (end before start) and hours() returned an empty list. Six
    # months of the year the whole product silently produced nothing.
    #
    # Midday is safe year round at 38 N: the Sun is up at 12:00 UTC on every
    # date, so the first crossing after it is always the sunset we want.
    start = day.astimezone(timezone.utc).replace(hour=12, minute=0, second=0,
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
