"""What Nerpio is doing right now, in one object (SPEC v8 section 3.1, note 7).

The observatory page already publishes everything an astrophotographer needs
before deciding whether to open the roof, but it publishes it as six unlabelled
tables mixing live sensors with units frozen since 2025, wind in km/h, and a
transition log whose newest line is not always the interesting one. Reading it
correctly takes about a minute of human attention, every time. This module does
that reading once and hands back a flat summary a panel can print.

Three deliberate positions, because they are the ones that go wrong:

* Nothing here re-parses the page. `workers.scrape_astrocamp.parse` already
  knows where the tables are and that wind arrives in km/h; a second parser
  would drift away from it silently. The only thing added is the safety-sensor
  strip at the top of the page (Humidity/Sky/SunAlt/Temperature), which `parse`
  does not extract today and which is the observatory's *current* sensor state,
  fresher than the transition log below it.
* The page is served over plain HTTP on a non-standard port and has been
  observed returning a partial document -- one download brought the
  CloudWatcher table and nothing else. A summary that quietly dropped the
  missing halves would look identical to a calm night. So `missing` is a field,
  not an exception, and `headline` says out loud when the download was
  incomplete or the sensors are stale.
* Ages are reported, never hidden. A reading is shown whether or not it passes
  `invariants.is_fresh`; what changes is that a stale one is labelled stale.
  Refusing to show an old number teaches the user nothing, and showing it
  unlabelled is a lie.

Nothing in this module models or predicts anything: every number is a sensor
reading or a subtraction of two sensor readings. The one piece of outside
knowledge is the dew-margin threshold, and it is flagged as such where it is
defined.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass, field

import requests

from .invariants import data_quality, is_fresh
from .site import NERPIO, Site
from .workers.scrape_astrocamp import URL, _rows, _sky_verdict, _tables, parse

# Dew margin = T_air - T_dew. This is the number that decides whether the
# corrector plate fogs over, not the relative humidity: 90% RH at 2 degrees of
# margin is survivable and 70% RH at 0.5 is not.
#
# PROVENANCE: these two thresholds are standard astrophotography practice
# (the usual "keep the optics a couple of degrees above the dew point" rule of
# thumb, which is also how dew-heater controllers are set up). They have NOT
# been measured at Nerpio and are not calibrated against any dewing event
# recorded by this system. Treat them as a warning to look, never as a
# prediction. Optics radiating to a cold sky sit *below* air temperature, so
# the true margin at the glass is smaller than the one computed here.
DEW_MARGIN_WATCH = 3.0     # degC: real risk of fogging
DEW_MARGIN_CRITICAL = 1.0  # degC: fogging is close to certain

# The five blocks `parse` returns; a good download has all of them.
PAGE_SECTIONS = ("cloudwatchers", "meteo", "roofs", "safety_log", "open_hours")

_SENSOR_NAME = re.compile(r"^[A-Za-z][A-Za-z ]*\(\d+\)$")
_STATUS = re.compile(r"Status:\s*(Safe|Unsafe)", re.I)

_SECTION_ES = {
    "cloudwatchers": "sensores de nube",
    "meteo": "estaciones meteo",
    "roofs": "techos",
    "safety_log": "registro de seguridad",
    "open_hours": "estadística de horas abiertas",
}


def extract_safety_sensors(page: str) -> dict[str, str]:
    """The green/red sensor strip at the top of the page, as name -> state.

    This is the observatory's own current verdict per sensor ('Sky (4)':
    'Clear', 'Humidity (1)': 'Dry', 'SunAlt (1)': 'Night'), and it is the only
    place on the page where those states are given for *now* rather than as a
    transition that happened at some past minute. `parse` skips this table
    because it has no header row to key on, so it is recovered here by shape:
    cells come in name/state pairs, and only the names carry the "Word (n)"
    form. The roof table has the same pair layout, hence the explicit exclusion
    of Open/Closed -- without it, every roof would be filed as a safety sensor.
    """
    sensors: dict[str, str] = {}
    for table in _tables(page):
        for row in _rows(table):
            for name, state in zip(row[::2], row[1::2]):
                if state in ("Open", "Closed") or not state:
                    continue
                if _SENSOR_NAME.match(name):
                    sensors[name] = state
    return sensors


def _latest(records: list[dict]) -> dict | None:
    """Newest record by timestamp, never by table position.

    The page lists a CloudWatcher frozen since July and a weather station
    frozen since 2025 above live ones, so "the first row" is a trap.
    """
    dated = [r for r in records if r.get("t_utc") is not None]
    return max(dated, key=lambda r: r["t_utc"]) if dated else None


def _safety_status(safety_log: list[dict]) -> str | None:
    """The observatory's own Safe/Unsafe flag, from the newest log line.

    Careful: the sensor named in a line is the one that *changed*, not the one
    that caused the status. "Status: Unsafe.  Sky1(4) is Clear.." is a real
    line from the page -- the sky had just cleared while the site was still
    unsafe for another reason. So the status and the sky verdict are read
    separately and never inferred from one another.
    """
    newest = _latest(safety_log)
    if newest is None:
        return None
    found = _STATUS.search(newest.get("message") or "")
    return found.group(1).capitalize() if found else None


def _sky_state(sensors: dict[str, str], safety_log: list[dict]) -> bool | None:
    """Clear/Cloudy, preferring the live sensor strip over the history log."""
    states = [v for k, v in sensors.items() if k.startswith("Sky")]
    if states:
        # Any sensor calling it cloudy wins: this feeds a "can I open" answer,
        # and the pessimistic reading is the safe one to show.
        if any(s == "Cloudy" for s in states):
            return False
        if all(s == "Clear" for s in states):
            return True
    return _sky_verdict(safety_log)


@dataclass
class SiteNow:
    """A flat, honest snapshot of the site, ready to print in a panel.

    Every field is either a sensor reading, a count, or None. None always means
    "the page did not give it", never zero, and the `missing`/`*_fresh` fields
    say why so the panel can grey a value out instead of showing a stale number
    as if it were current.
    """

    site_id: str
    retrieved_at: dt.datetime
    # Outside air, from the freshest weather station on the page.
    t_utc: dt.datetime | None = None
    t_out: float | None = None            # degC
    rh_out: float | None = None           # %
    dew_point: float | None = None        # degC
    wind_ms: float | None = None          # m/s (the page publishes km/h)
    wind_dir: float | None = None         # deg
    pressure: float | None = None         # hPa, station level (site is 1650 m)
    # Sky, from the freshest CloudWatcher. sky_temp is an AAG-style IR sky
    # temperature, not a broadband radiative temperature (SPEC v8 s. 2.6).
    sky_t_utc: dt.datetime | None = None
    sky_temp: float | None = None         # degC
    sky_ambient: float | None = None      # degC, the CloudWatcher's own probe
    sky_index: float | None = None
    # Operational state of the site itself.
    roofs_open: int = 0
    roofs_total: int = 0
    safety_status: str | None = None      # "Safe" / "Unsafe", as published
    sky_clear: bool | None = None
    sensors: dict[str, str] = field(default_factory=dict)
    # What the download did not contain, by `parse` key.
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Whether the page came back whole. It intermittently does not."""
        return not self.missing

    @property
    def missing_note(self) -> str | None:
        """User-facing Spanish note naming the blocks that never arrived."""
        if not self.missing:
            return None
        names = ", ".join(_SECTION_ES.get(part, part) for part in self.missing)
        return "Descarga incompleta: falta " + names

    @property
    def age(self) -> dt.timedelta | None:
        """How old the weather reading is at the moment of the download."""
        if self.t_utc is None:
            return None
        return self.retrieved_at - self.t_utc

    @property
    def age_minutes(self) -> float | None:
        return self.age.total_seconds() / 60 if self.age is not None else None

    @property
    def meteo_fresh(self) -> bool:
        return is_fresh(self.t_utc)

    @property
    def sky_fresh(self) -> bool:
        return is_fresh(self.sky_t_utc)

    @property
    def dew_margin(self) -> float | None:
        """T_air - T_dew, in degC: the number that decides fogging.

        Measured, in the sense that both terms come from the same station in
        the same row. What is NOT measured is the temperature of the glass,
        which radiates to the sky and sits below air temperature -- so this is
        an optimistic bound on the margin at the corrector.
        """
        if self.t_out is None or self.dew_point is None:
            return None
        return self.t_out - self.dew_point

    @property
    def dew_risk(self) -> str:
        """'desconocido' | 'bajo' | 'vigilar' | 'crítico'.

        Thresholds are literature/practice, not site calibration: see
        DEW_MARGIN_WATCH above.
        """
        margin = self.dew_margin
        if margin is None:
            return "desconocido"
        if margin < DEW_MARGIN_CRITICAL:
            return "crítico"
        if margin < DEW_MARGIN_WATCH:
            return "vigilar"
        return "bajo"

    @property
    def dew_warning(self) -> str | None:
        """Spanish warning to show when the corrector is at risk, else None."""
        margin = self.dew_margin
        if margin is None:
            return None
        if margin < DEW_MARGIN_CRITICAL:
            return (
                "Rocío casi seguro: solo %.1f °C de margen sobre el punto "
                "de rocío. Resistencias al máximo (criterio de práctica "
                "astrofotográfica estándar, no medido aquí)." % margin
            )
        if margin < DEW_MARGIN_WATCH:
            return (
                "Riesgo de empañamiento: %.1f °C de margen sobre el punto "
                "de rocío. Enciende las resistencias (criterio de práctica "
                "astrofotográfica estándar, no medido aquí)." % margin
            )
        return None

    @property
    def headline(self) -> str:
        """One Spanish sentence for the top of the panel.

        Built so the caveats come first: an incomplete download or a stale
        sensor changes what the numbers mean, and a headline that buried that
        after the temperature would be read as if it were live. When the
        download brought nothing usable at all the sentence says exactly that,
        instead of dressing an empty summary up as a report about Nerpio.
        """
        caveats: list[str] = []
        if not self.complete:
            caveats.append(self.missing_note)
        if self.t_utc is not None and not self.meteo_fresh:
            caveats.append("Lectura CADUCADA de hace %d min" % round(self.age_minutes))

        values: list[str] = []
        if self.t_out is not None:
            values.append("%.1f °C" % self.t_out)
        if self.rh_out is not None:
            values.append("%.0f%% HR" % self.rh_out)
        if self.dew_margin is not None:
            values.append("margen de rocío %.1f °C" % self.dew_margin)
        if self.wind_ms is not None:
            values.append("viento %.1f m/s" % self.wind_ms)
        if self.sky_temp is not None:
            values.append("cielo IR %.1f °C" % self.sky_temp)
        if self.sky_clear is not None:
            values.append("despejado" if self.sky_clear else "nublado")
        if self.roofs_total:
            values.append("%d/%d techos abiertos" % (self.roofs_open, self.roofs_total))
        if self.safety_status:
            values.append(
                "observatorio en %s"
                % ("seguro" if self.safety_status == "Safe" else "NO seguro")
            )

        if not values:
            text = "Sin datos utilizables de la página del observatorio."
            return text + " " + self.missing_note + "." if self.missing_note else text

        text = "AstroCamp ahora: " + ", ".join(caveats + values) + "."
        warning = self.dew_warning
        return text + " " + warning if warning else text


def summarise(
    parsed: dict,
    *,
    sensors: dict[str, str] | None = None,
    retrieved_at: dt.datetime | None = None,
    site: Site = NERPIO,
) -> SiteNow:
    """Build the snapshot from an already-parsed page, so tests need no network.

    `parsed` is whatever `scrape_astrocamp.parse` returned, including a partial
    result: every lookup below tolerates an empty or absent block, and what was
    absent is recorded in `missing` rather than defaulted to a plausible value.
    """
    now = retrieved_at or dt.datetime.now(dt.timezone.utc)
    sensors = dict(sensors or {})

    meteo = _latest(parsed.get("meteo") or []) or {}
    cloud = _latest(parsed.get("cloudwatchers") or []) or {}
    roofs = parsed.get("roofs") or {}
    log = parsed.get("safety_log") or []

    quality = data_quality({name: bool(parsed.get(name)) for name in PAGE_SECTIONS})

    return SiteNow(
        site_id=site.site_id,
        retrieved_at=now,
        t_utc=meteo.get("t_utc"),
        t_out=meteo.get("t_out"),
        rh_out=meteo.get("rh_out"),
        dew_point=meteo.get("dew_point"),
        wind_ms=meteo.get("wind_ms"),
        wind_dir=meteo.get("wind_dir"),
        pressure=meteo.get("pressure"),
        sky_t_utc=cloud.get("t_utc"),
        sky_temp=cloud.get("sky_temp"),
        sky_ambient=cloud.get("ambient"),
        sky_index=cloud.get("sky_index"),
        roofs_open=sum(1 for state in roofs.values() if state),
        roofs_total=len(roofs),
        safety_status=_safety_status(log),
        sky_clear=_sky_state(sensors, log),
        sensors=sensors,
        missing=list(quality["missing"]),
    )


def fetch(url: str = URL, *, timeout: float = 45, site: Site = NERPIO) -> SiteNow:
    """Download the page once and summarise it.

    Deliberately does not archive anything: `workers.scrape_astrocamp.run` owns
    the raw store and the canonical rows, and a panel refresh must not be able
    to write a second, differently-timestamped copy of the same reading into
    the archive.
    """
    retrieved_at = dt.datetime.now(dt.timezone.utc)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    page = response.text
    return summarise(
        parse(page),
        sensors=extract_safety_sensors(page),
        retrieved_at=retrieved_at,
        site=site,
    )


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(fetch().headline)
