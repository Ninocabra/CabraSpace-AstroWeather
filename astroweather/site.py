"""Site identity contract (SPEC v8 section 0).

Every elevation figure here was verified against the live Open-Meteo API on
2026-08-27. The three elevations are NOT interchangeable: mixing them up is the
bug that survived three document revisions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    """A site the system can forecast for."""

    site_id: str
    name: str
    # Exact coordinates. Rounding them changes the DEM cell Open-Meteo picks
    # (38.16/-2.32 yields a 1302 m DEM instead of 1542 m).
    latitude: float
    longitude: float
    # Real elevation of the observatory, in metres.
    site_elevation: float
    # Real elevation of the NWP grid cell, obtained with `&elevation=nan`.
    # Used only for the aerosol column correction (delta_h).
    model_grid_elevation: float
    timezone: str = "UTC"
    mpc_code: str | None = None
    # Reference sky background of the site (mag/arcsec^2), for q_dark. The
    # photometer measures 21.71 at the zenith away from the ecliptic, but in
    # the TESS band; this V-band figure stays as the quality reference until a
    # band calibration exists (skybackground.py, SPEC v8 section 2.3).
    b_ref_mag: float = 21.9

    @property
    def delta_h(self) -> float:
        """Height of the site above the model topography, in metres.

        Feeds the non-dust term of the AOD correction (SPEC v8 section 2.1).
        """
        return max(0.0, self.site_elevation - self.model_grid_elevation)


NERPIO = Site(
    site_id="nerpio",
    name="AstroCamp, Nerpio (Albacete)",
    latitude=38.16583,
    longitude=-2.32672,
    site_elevation=1650.0,
    model_grid_elevation=1067.0,
    timezone="Europe/Madrid",
    mpc_code="I79",
)

SITES = {site.site_id: site for site in (NERPIO,)}
