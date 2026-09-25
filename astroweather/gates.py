"""Can the dome open, and is the night worth it (SPEC v8 sections 1.1 and 1.4).

Three questions kept apart on purpose, because they are not the same kind of
thing and multiplying them together is what made the old single score
uninterpretable:

* SAFETY is close to binary. Rain on the sensor, a gust above the closure
  limit, or a storm overhead: the roof stays shut, and no amount of good seeing
  changes that.
* USABILITY is a probability, and a calibrated one (calibration.py).
* QUALITY is per dimension, and lives elsewhere.

Storm risk is deliberately escalated by horizon rather than gated on CAPE.
CAPE says the atmosphere COULD convect, not that anything is falling on the
observatory: using it as a hard gate closes the dome on plenty of usable
nights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Wind, in m/s (the archive is m/s everywhere; the page that publishes km/h is
# converted at ingestion). 15 km/h ideal, 30 degrades guiding, 40 closes.
GUST_IDEAL = 4.2
GUST_DEGRADED = 8.3
GUST_CLOSE = 11.1
# Precipitation probability above which the night is treated as wet.
PRECIP_PROBABILITY_LIMIT = 30.0
# CAPE that raises the risk flag. It never closes the roof on its own.
CAPE_ELEVATED = 300.0
CAPE_HIGH = 1000.0


@dataclass(frozen=True)
class Risk:
    """A named risk with its level and the reason, in the user's words."""

    name: str
    level: str            # 'ok' | 'watch' | 'blocked'
    reason: str

    @property
    def blocks(self) -> bool:
        return self.level == "blocked"


@dataclass(frozen=True)
class SafetyVerdict:
    t_utc: datetime
    risks: list[Risk] = field(default_factory=list)

    @property
    def safe_to_open(self) -> bool:
        return not any(risk.blocks for risk in self.risks)

    @property
    def blocking(self) -> list[Risk]:
        return [risk for risk in self.risks if risk.blocks]

    @property
    def summary(self) -> str:
        if self.safe_to_open:
            watching = [r for r in self.risks if r.level == "watch"]
            if watching:
                return "abrible, con vigilancia: " + "; ".join(r.reason for r in watching)
            return "abrible"
        return "NO abrir: " + "; ".join(risk.reason for risk in self.blocking)


def _rain_risk(precipitation: float | None, probability: float | None,
               observed_rain: bool | None, hours_ahead: float,
               precipitation_model: str | None = None) -> Risk:
    """Rain, weighted by what can actually be known at that horizon."""
    if observed_rain:
        return Risk("lluvia", "blocked", "el sensor local está detectando lluvia")
    if precipitation is not None and precipitation > 0.1:
        # WeatherNext's millimetres are its raw data, which its terms do not
        # let us republish: the verdict is ours, the number stays in.
        if precipitation_model and precipitation_model.startswith("weathernext"):
            return Risk("lluvia", "blocked", "precipitación prevista (WeatherNext)")
        return Risk("lluvia", "blocked", f"precipitación prevista {precipitation:.1f} mm")
    if probability is not None and probability > PRECIP_PROBABILITY_LIMIT:
        # Far out, a high probability is a warning; close in, it is a decision.
        level = "blocked" if hours_ahead <= 6 else "watch"
        return Risk("lluvia", level, f"probabilidad de lluvia {probability:.0f} %")
    return Risk("lluvia", "ok", "sin lluvia prevista")


def _wind_risk(gust: float | None) -> Risk:
    if gust is None:
        return Risk("viento", "ok", "sin dato de racha")
    if gust >= GUST_CLOSE:
        return Risk("viento", "blocked",
                    f"rachas de {gust * 3.6:.0f} km/h, por encima del cierre")
    if gust >= GUST_DEGRADED:
        return Risk("viento", "watch",
                    f"rachas de {gust * 3.6:.0f} km/h: el guiado sufre en focal larga")
    return Risk("viento", "ok", f"viento flojo ({gust * 3.6:.0f} km/h)")


def _storm_risk(cape: float | None, probability: float | None,
                hours_ahead: float) -> Risk:
    """Escalated by horizon: sensors decide now, the ensemble only warns later."""
    if cape is None:
        return Risk("tormenta", "ok", "sin dato de CAPE")
    if hours_ahead <= 1:
        # In the next hour only what is measured counts; CAPE is not an event.
        return Risk("tormenta", "ok", "a esta hora mandan el radar y el sensor")
    if cape >= CAPE_HIGH and (probability or 0) > 20:
        return Risk("tormenta", "blocked",
                    f"CAPE {cape:.0f} J/kg con {probability:.0f} % de precipitación")
    if cape >= CAPE_ELEVATED:
        return Risk("tormenta", "watch",
                    f"CAPE {cape:.0f} J/kg: posible desarrollo convectivo")
    return Risk("tormenta", "ok", "atmosfera estable")


def evaluate(*, t_utc: datetime, hours_ahead: float,
             gust: float | None = None,
             precipitation: float | None = None,
             precipitation_probability: float | None = None,
             cape: float | None = None,
             observed_rain: bool | None = None,
             hardware_ok: bool = True,
             precipitation_model: str | None = None) -> SafetyVerdict:
    """Safety verdict for one hour, with the reason attached to every risk."""
    risks = [
        _rain_risk(precipitation, precipitation_probability, observed_rain, hours_ahead,
                   precipitation_model),
        _wind_risk(gust),
        _storm_risk(cape, precipitation_probability, hours_ahead),
    ]
    if not hardware_ok:
        risks.append(Risk("equipo", "blocked", "el observatorio reporta un problema"))
    return SafetyVerdict(t_utc, risks)


def probability_usable(clear_probability: float, verdict: SafetyVerdict) -> float:
    """Chance the hour is actually usable: clear AND safe to be open.

    The safety verdict is close to binary, so it multiplies rather than being
    folded into the probability; a blocked hour is unusable no matter how
    beautifully clear it is.
    """
    if not verdict.safe_to_open:
        return 0.0
    # A watched risk is not a blocker, but it does eat into the odds.
    watches = sum(1 for risk in verdict.risks if risk.level == "watch")
    return float(clear_probability * (0.85 ** watches))


def opening_window(usable_hours: list[float], minimum_hours: float = 2.0,
                   step_hours: float = 1.0) -> tuple[int, int, float] | None:
    """The best window of at least `minimum_hours`: (first, last, floor).

    The floor is the WEAKER hour of the window, on purpose. Adjacent hours
    share the same forecast error -- a front that arrives late is late for both
    -- so multiplying their probabilities would count the same doubt twice, and
    taking the best hour alone was the failure this replaces: 74 published
    verdicts, zero "Cerrado", because any night with one clear hour at 03 UTC
    scored as that hour (measured 2026-09-04 against the roof record).
    """
    if not usable_hours:
        return None
    slots = max(1, int(round(minimum_hours / step_hours)))
    if len(usable_hours) < slots:
        return (0, len(usable_hours) - 1, float(min(usable_hours)))
    best = None
    for first in range(len(usable_hours) - slots + 1):
        floor = float(min(usable_hours[first:first + slots]))
        if best is None or floor > best[2]:
            best = (first, first + slots - 1, floor)
    return best


def opening_probability(usable_hours: list[float], climatology: float | None = None,
                        minimum_hours: float = 2.0, step_hours: float = 1.0) -> float:
    """Chance the night offers at least `minimum_hours` in a row worth opening.

    It is the floor of the best window (see `opening_window`), so a night is
    only "Abierto" when SOME two consecutive hours both clear the bar. With the
    roof-trained curve (roofcalibration.py) the per-hour number is already the
    chance the observatory opens at that hour given the forecast cloud, so the
    verdict reads as the roof would: a two-hour window is what they open for.

    Climatology enters as a prior on nights we know nothing about, never as a
    multiplier on an already calibrated probability -- that would count the
    same base rate twice.
    """
    window = opening_window(usable_hours, minimum_hours, step_hours)
    if window is None:
        return climatology if climatology is not None else 0.0
    return window[2]
