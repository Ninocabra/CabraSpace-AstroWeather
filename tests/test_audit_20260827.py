"""Regressions for the four defects found in the external audit of 2026-08-27.

Each of these was a real published number that was wrong. They live in their
own file because the failure they guard against is not "the physics drifted"
but "somebody reverted a correction without knowing why it was there".
"""

import numpy as np

from astroweather.calibration import (CLEAR_LEVEL_CENTRE, CLEAR_LEVEL_TOLERANCE,
                                      CLEAR_SCATTER_THRESHOLD)
from astroweather.moonsky import mag_to_nl, nl_to_mag
from astroweather.seeing import integrate
from astroweather.targets import (GATE_SHOULDER_MAG, MAX_SHARPNESS_GAIN,
                                  MOON_FILTER_FACTOR, SKY_GATE, hourly_yield)


def _is_clear(values) -> bool:
    """The truth rule as observed_clear_hours applies it."""
    return (float(np.std(values)) < CLEAR_SCATTER_THRESHOLD
            and abs(float(np.mean(values)) - CLEAR_LEVEL_CENTRE)
            < CLEAR_LEVEL_TOLERANCE)


def test_stable_overcast_is_no_longer_filed_as_clear():
    """A flat stratus deck holds the photometer steady 1.5 mag off the model.

    Scatter alone called that hour clear, and 22 % of the training truth was
    built from hours like it -- 97 % of them darker, because cloud over a site
    with no city below swallows the airglow instead of reflecting streetlight.
    """
    overcast = [-1.52, -1.48, -1.50, -1.51, -1.49, -1.50]
    assert float(np.std(overcast)) < CLEAR_SCATTER_THRESHOLD   # steady...
    assert not _is_clear(overcast)                             # ...but not clear

    genuine = [0.02, -0.04, 0.01, -0.06, 0.03, -0.02]
    assert _is_clear(genuine)


def test_narrowband_survives_astronomical_twilight():
    """The filter cuts the whole sky, not only the Moon.

    Attenuating just the lunar term left narrowband facing an unfiltered sky
    and zeroed it through twilight -- backwards for the one technique that
    works there.
    """
    factor = MOON_FILTER_FACTOR["narrowband"]
    twilight = nl_to_mag(mag_to_nl(19.0) * factor)
    assert hourly_yield(airmass=1.0, sky_mag=twilight, seeing_arcsec=2.0,
                        k_total=0.18, alpha=0.0, difficulty="narrowband") > 0.5

    # Still honest at the bright end: nautical twilight remains unusable.
    # It lands a thousandth of a magnitude inside the shoulder, so the test
    # asks for negligible, not for a hard zero that floating point cannot give.
    nautical = nl_to_mag(mag_to_nl(17.0) * factor)
    assert hourly_yield(airmass=1.0, sky_mag=nautical, seeing_arcsec=2.0,
                        k_total=0.18, alpha=0.0, difficulty="narrowband") < 0.02


def test_class_gate_is_a_shoulder_not_a_cliff():
    """0.02 mag used to swing an IFN hour from 0.000 to 0.685."""
    gate = SKY_GATE["extremo_contraste"]
    below = hourly_yield(airmass=1.0, sky_mag=gate - 0.01, seeing_arcsec=2.0,
                         k_total=0.18, alpha=0.0, difficulty="extremo_contraste")
    above = hourly_yield(airmass=1.0, sky_mag=gate + 0.01, seeing_arcsec=2.0,
                         k_total=0.18, alpha=0.0, difficulty="extremo_contraste")
    # A smoothstep is steepest exactly at its centre, which is where the gate
    # sits, so this is the worst case: 0.075 across those two hundredths where
    # the old step gave 0.685. An order of magnitude is the claim being made,
    # not perfect smoothness.
    assert abs(above - below) < 0.1, "the gate is stepping again"
    assert abs(above - below) < 0.685 / 8

    # The gate keeps its meaning: half value where the hard cut used to be.
    dark = hourly_yield(airmass=1.0, sky_mag=gate + 1.0, seeing_arcsec=2.0,
                        k_total=0.18, alpha=0.0, difficulty="extremo_contraste")
    at_gate = hourly_yield(airmass=1.0, sky_mag=gate, seeing_arcsec=2.0,
                           k_total=0.18, alpha=0.0, difficulty="extremo_contraste")
    assert 0.3 < at_gate / dark < 0.7

    # It must still reach zero and full value at the ends of the shoulder.
    assert hourly_yield(airmass=1.0, sky_mag=gate - GATE_SHOULDER_MAG,
                        seeing_arcsec=2.0, k_total=0.18, alpha=0.0,
                        difficulty="extremo_contraste") == 0.0


def test_uncalibrated_seeing_cannot_inflate_compact_targets():
    """We publish terciles; the yield must not read the loose number.

    The model runs optimistic (1.21 predicted where 3.16 was measured), so a
    favourable night saturates the cap instead of buying a factor four.
    """
    good, better = integrate(25.0), integrate(10.0)
    assert good.tercile == better.tercile == "favorable"
    yields = [hourly_yield(airmass=1.0, sky_mag=21.5,
                           seeing_arcsec=t.range_arcsec[1], k_total=0.18,
                           alpha=1.0, difficulty="compacto_resolucion")
              for t in (good, better)]
    assert abs(yields[0] - yields[1]) < 1e-9, "same tercile, different answer"
    assert max(yields) <= MAX_SHARPNESS_GAIN


def test_twilight_never_extrapolates_past_its_fit():
    """It had a floor at -20 deg and no ceiling, so an exponential ran away.

    At +11.7 deg of Sun altitude the clear-sky model returned -0.60 mag/arcsec2
    -- a sky brighter than the Moon's own disc -- and printed it beside numbers
    that had been measured.
    """
    import datetime as dt

    from astroweather.skymodel import DAYLIGHT_ZENITH_MAG, TWILIGHT_FIT_MAX, predict

    times = [dt.datetime(2026, 8, 27, 12, tzinfo=dt.timezone.utc),
             dt.datetime(2026, 8, 27, 17, 42, tzinfo=dt.timezone.utc),
             dt.datetime(2026, 8, 27, 21, 12, tzinfo=dt.timezone.utc)]
    for entry in predict(times):
        assert entry.predicted_mag >= DAYLIGHT_ZENITH_MAG
        above = entry.sun_altitude > TWILIGHT_FIT_MAX
        assert entry.twilight_extrapolated is above
        assert entry.is_trustworthy is not above


def test_every_month_of_the_year_produces_a_night():
    """From October to March the product silently produced nothing at all.

    night_for scanned forward from 06:00 UTC on the assumption that the Sun was
    already up when the scan began. True in summer; false at this latitude for
    half the year, when 06:00 is still night. The first crossing found was then
    a SUNRISE, so the window came out with its end before its start and hours()
    returned an empty list -- no frames, no targets, no forecast, no error.

    Found by a test written against the roof model, which asked for a January
    night and got zero expected open hours out of a climatological 5.5.
    """
    import datetime as dt

    from astroweather.ephem import night_for

    for month in range(1, 13):
        night = night_for(dt.datetime(2026, month, 15, tzinfo=dt.timezone.utc))
        assert night.sunset is not None and night.sunrise is not None
        assert night.window_start < night.window_end, f"mes {month} invertido"
        assert len(night.hours(30)) > 8, f"mes {month} sin frames"
        # Astronomical darkness at 38 N runs from about 5 h in June to 11 h in
        # December. Anything outside that means the crossings were mispaired.
        assert 4.5 < night.dark_hours < 12.0, f"mes {month}: {night.dark_hours} h"
