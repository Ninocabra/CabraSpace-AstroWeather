"""Tests for the FWHM pipeline (SPEC v8 sections 2.5, 4.1 and 4.4)."""

import math

import pytest

from astroweather.workers.fwhm_pipeline import (
    MAX_ECCENTRICITY, pixel_scale_arcsec, setup_id, airmass, to_zenith_500nm,
)


class Header(dict):
    """A FITS header behaves like a dict for what this module reads."""


def test_pixel_scale_from_the_optics_in_the_header():
    # ASI2600MM (3.76 um) on the 9.25" SCT at 1525 mm -> about half an arcsec.
    header = Header(XPIXSZ=3.76, FOCALLEN=1525.0, XBINNING=1)
    assert pixel_scale_arcsec(header) == pytest.approx(0.509, abs=0.002)


def test_binning_doubles_the_pixel_scale():
    header = Header(XPIXSZ=3.76, FOCALLEN=1525.0, XBINNING=2)
    assert pixel_scale_arcsec(header) == pytest.approx(1.017, abs=0.004)


def test_setup_identity_changes_with_the_optical_train():
    """Swapping camera or focal length must not look like a change in the sky."""
    first = setup_id(Header(TELESCOP="CELESTRON SCT 9,25", INSTRUME="ZWO ASI2600MM Pro",
                            FOCALLEN=1525.0, XBINNING=1))
    reducer = setup_id(Header(TELESCOP="CELESTRON SCT 9,25", INSTRUME="ZWO ASI2600MM Pro",
                              FOCALLEN=1050.0, XBINNING=1))
    assert first != reducer
    assert "ASI2600MM" in first


def test_airmass_prefers_the_recorded_value():
    assert airmass(Header(AIRMASS=1.427, CENTALT=30.0)) == pytest.approx(1.427)


def test_airmass_falls_back_to_the_pointing_altitude():
    assert airmass(Header(CENTALT=30.0)) == pytest.approx(2.0, abs=0.01)
    assert airmass(Header()) is None


def test_zenith_correction_reduces_a_low_altitude_measurement():
    """Seeing grows as X^0.6, so a frame at X=1.4 reads worse than the zenith."""
    at_zenith = to_zenith_500nm(4.26, 1.43, "L")
    assert at_zenith < 4.26
    assert at_zenith == pytest.approx(4.26 / 1.43 ** 0.6 * (500 / 550) ** -0.2, rel=1e-6)


def test_red_filters_are_normalised_upwards():
    """Ha sees a calmer sky than 500 nm, so its FWHM must be scaled up."""
    assert to_zenith_500nm(2.0, 1.0, "HA") > 2.0
    assert to_zenith_500nm(2.0, 1.0, "B") < 2.0


def test_unknown_filter_leaves_the_wavelength_alone():
    assert to_zenith_500nm(2.0, 1.0, "WEIRD") == pytest.approx(2.0)


def test_trailing_threshold_is_below_a_round_star():
    """A circular star has eccentricity 0; the cut must not reject good frames."""
    assert 0 < MAX_ECCENTRICITY < 1
    assert MAX_ECCENTRICITY > 0.4      # some elongation is always present
