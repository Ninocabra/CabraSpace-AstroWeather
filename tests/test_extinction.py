"""Tests for atmospheric extinction (SPEC v8 section 2.1)."""

import pytest

from astroweather.extinction import (
    ALPHA_DUST, angstrom_from_species, evaluate, exposure_penalty, quality,
    rayleigh, total_k, transmission,
)
from astroweather.site import NERPIO


def test_altitude_thins_the_molecular_extinction():
    """1650 m is already 15 % less Rayleigh than sea level."""
    assert rayleigh(1650) == pytest.approx(0.118, abs=0.002)
    assert rayleigh(1650) < rayleigh(0)


def test_dust_is_not_scaled_down_by_the_site_height():
    """Saharan dust rides above the observatory; the rest of the aerosol does not."""
    dusty = evaluate(aod_total=0.40, aod_dust=0.30, elevation_m=1650,
                     delta_h_m=NERPIO.delta_h)
    clean_column = evaluate(aod_total=0.40, aod_dust=0.0, elevation_m=1650,
                            delta_h_m=NERPIO.delta_h)
    assert dusty.aod_site > clean_column.aod_site


def test_dust_and_smoke_colour_the_sky_differently():
    """Coarse dust is neutral; fine smoke eats the blue, where OIII lives."""
    assert angstrom_from_species(0.30, 0.28) == pytest.approx(ALPHA_DUST, abs=0.2)
    assert angstrom_from_species(0.30, 0.01) > 1.3

    dust = evaluate(aod_total=0.30, aod_dust=0.28, elevation_m=1650, delta_h_m=583)
    smoke = evaluate(aod_total=0.30, aod_dust=0.01, elevation_m=1650, delta_h_m=583)
    assert "neutra" in dust.color_note
    assert "azul" in smoke.color_note


def test_the_dust_alert_fires_on_a_real_episode():
    calima = evaluate(aod_total=0.45, aod_dust=0.35, elevation_m=1650, delta_h_m=583)
    assert calima.dust_alert
    assert "calima" in calima.verdict
    assert not evaluate(aod_total=0.06, aod_dust=0.01, elevation_m=1650,
                        delta_h_m=583).dust_alert


def test_extinction_costs_exposure_faster_than_it_costs_magnitudes():
    """SNR grows as the root of time, so 0.3 mag lost is ~75 % more exposure."""
    penalty = exposure_penalty(0.18 + 0.3, airmass=1.0)
    assert penalty == pytest.approx(10 ** (0.8 * 0.3), rel=1e-6)
    assert penalty > 1.5


def test_a_photometric_night_costs_nothing_extra():
    assert exposure_penalty(0.18, airmass=1.0) == pytest.approx(1.0)
    assert quality(0.18) == pytest.approx(1.0)


def test_transmission_falls_with_airmass():
    assert transmission(0.2, 1.0) > transmission(0.2, 2.0)
    assert 0 < transmission(0.6, 3.0) < 1


def test_missing_species_is_flagged_not_guessed():
    partial = evaluate(aod_total=0.30, aod_dust=None, elevation_m=1650, delta_h_m=583)
    assert not partial.species_resolved
    assert not partial.dust_alert          # no species, no alert claimed


def test_no_aerosol_data_still_yields_the_molecular_floor():
    blind = evaluate(aod_total=None, aod_dust=None, elevation_m=1650, delta_h_m=583)
    assert blind.k_total == pytest.approx(total_k(0.0, 1650))
