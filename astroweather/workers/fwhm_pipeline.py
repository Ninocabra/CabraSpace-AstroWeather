"""Seeing ground truth from real subframes (SPEC v8 sections 4.1 and 6.1).

This is the only measurement that can ever verify the seeing forecast, and the
one piece of ground truth nobody else can hand us. Every night that passes
without it is unrecoverable, which is why it belongs in phase 0 even though
the seeing model itself lands much later.

What it does per frame: find stars, measure their shape by second moments,
take a robust FWHM, convert pixels to arcseconds from the header optics, and
normalise to zenith and 500 nm so that frames from different nights, targets
and filters are comparable.

Two filters that decide whether the number means anything:

* Eccentricity. Wind shake, guiding errors and periodic error stretch stars
  into ellipses; the atmosphere blurs them into circles. Elongated frames are
  measuring the mount, not the sky, and are dropped.
* setup_id. Telescope, camera, binning and focal length are recorded with
  every row: swapping the optical train must never look like a change in the
  atmosphere.

And the honest caveat: what comes out is IMAGE QUALITY, not seeing. It carries
the atmosphere plus focus, optics, guiding and mount, so a single frame is an
upper bound on the seeing. The atmospheric part is recovered the way SPEC v8
section 4.4 prescribes -- a low percentile within each night, per setup_id, so
the instrumental floor of that train can be subtracted -- never by trusting one
number. The eccentricity flag is the first line of that defence.
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from photutils.detection import DAOStarFinder
from photutils.morphology import data_properties

from ..archive import utcnow, write_canonical
from ..site import NERPIO, Site

ARCSEC_PER_RADIAN = 206264.806
# Above this median eccentricity the stars are trailed, not seeing-limited.
MAX_ECCENTRICITY = 0.55
MIN_STARS = 8
MAX_STARS = 120
STAMP_RADIUS = 12            # pixels around each star for the moment analysis
REFERENCE_WAVELENGTH_NM = 500.0

# Effective wavelengths of the usual filters, for the lambda^(-1/5) scaling.
FILTER_WAVELENGTH_NM = {
    "L": 550.0, "LUM": 550.0, "R": 640.0, "G": 530.0, "B": 470.0,
    "HA": 656.3, "H": 656.3, "OIII": 500.7, "O3": 500.7, "SII": 672.4, "S2": 672.4,
}


def pixel_scale_arcsec(header) -> float | None:
    """Arcseconds per pixel from the optics recorded in the header."""
    pixel_um = header.get("XPIXSZ")
    focal_mm = header.get("FOCALLEN")
    if not pixel_um or not focal_mm:
        return None
    binning = header.get("XBINNING", 1) or 1
    return ARCSEC_PER_RADIAN * (pixel_um * binning * 1e-3) / focal_mm


def setup_id(header) -> str:
    """Identity of the optical train: a change here is not a change in the sky."""
    parts = [
        str(header.get("TELESCOP", "?")).strip(),
        str(header.get("INSTRUME", "?")).strip(),
        f"f{int(header.get('FOCALLEN', 0))}",
        f"bin{header.get('XBINNING', 1)}",
    ]
    return "|".join(parts).replace(" ", "_")


def airmass(header) -> float | None:
    """Prefer the recorded airmass; fall back to the pointing altitude."""
    value = header.get("AIRMASS")
    if value:
        return float(value)
    altitude = header.get("CENTALT") or header.get("OBJCTALT")
    if altitude is None:
        return None
    altitude = float(altitude)
    if altitude <= 3:
        return None
    return 1.0 / math.sin(math.radians(altitude))


def measure_frame(path: Path) -> dict | None:
    """Median FWHM and eccentricity of the stars in one subframe."""
    with fits.open(path, memmap=False) as hdul:
        header = hdul[0].header
        if str(header.get("IMAGETYP", "")).upper().startswith(("BIAS", "DARK", "FLAT")):
            return None
        image = hdul[0].data.astype(np.float32)

    mean, median, sigma = sigma_clipped_stats(image, sigma=3.0)
    scale = pixel_scale_arcsec(header)
    if scale is None or sigma <= 0:
        return None

    # A 4" starting guess in pixels keeps the finder honest on bad nights too.
    finder = DAOStarFinder(fwhm=max(2.5, 4.0 / scale), threshold=8.0 * sigma,
                           exclude_border=True)
    sources = finder(image - median)
    if sources is None or len(sources) < MIN_STARS:
        return None

    sources.sort("flux", reverse=True)
    # photutils renamed the centroid columns in 3.x; support both.
    x_column = "x_centroid" if "x_centroid" in sources.colnames else "xcentroid"
    y_column = "y_centroid" if "y_centroid" in sources.colnames else "ycentroid"
    widths, eccentricities = [], []
    for row in sources[:MAX_STARS]:
        x, y = int(row[x_column]), int(row[y_column])
        if not (STAMP_RADIUS < x < image.shape[1] - STAMP_RADIUS
                and STAMP_RADIUS < y < image.shape[0] - STAMP_RADIUS):
            continue
        stamp = image[y - STAMP_RADIUS:y + STAMP_RADIUS + 1,
                      x - STAMP_RADIUS:x + STAMP_RADIUS + 1] - median
        try:
            properties = data_properties(stamp)
            major = float(properties.semimajor_axis.value)
            minor = float(properties.semiminor_axis.value)
        except Exception:
            continue
        if not (0 < minor <= major) or major > STAMP_RADIUS:
            continue
        # Circularised sigma, so an elongated star does not inflate the width.
        widths.append(2.3548 * math.sqrt(major * minor))
        eccentricities.append(float(properties.eccentricity.value))

    if len(widths) < MIN_STARS:
        return None

    fwhm_pixels = float(np.median(widths))
    eccentricity = float(np.median(eccentricities))
    fwhm_arcsec = fwhm_pixels * scale

    observed = header.get("DATE-OBS")
    t_utc = (datetime.fromisoformat(observed).replace(tzinfo=timezone.utc)
             if observed else None)

    return {
        "t_utc": t_utc,
        "fwhm_arcsec": fwhm_arcsec,
        "eccentricity": eccentricity,
        "stars": len(widths),
        "airmass": airmass(header),
        "filter": str(header.get("FILTER", "")).upper().strip(),
        "setup_id": setup_id(header),
        "object": header.get("OBJECT"),
        "pixel_scale": scale,
    }


def to_zenith_500nm(fwhm_arcsec: float, air_mass: float | None,
                    filter_name: str) -> float:
    """Normalise so frames from different nights and filters are comparable.

    Seeing grows as X^0.6 with airmass and improves slowly towards the red as
    lambda^(-1/5), both from SPEC v8 section 2.5.
    """
    corrected = fwhm_arcsec
    if air_mass and air_mass >= 1:
        corrected /= air_mass ** 0.6
    wavelength = FILTER_WAVELENGTH_NM.get(filter_name)
    if wavelength:
        corrected *= (REFERENCE_WAVELENGTH_NM / wavelength) ** (-0.2)
    return corrected


def to_row(measurement: dict, site: Site, retrieved_at: datetime) -> dict:
    zenith = to_zenith_500nm(measurement["fwhm_arcsec"], measurement["airmass"],
                             measurement["filter"])
    trailed = measurement["eccentricity"] > MAX_ECCENTRICITY
    return {
        "site_id": site.site_id,
        "t_utc": measurement["t_utc"],
        "fwhm_zenith_500nm": zenith,
        "fwhm_eccentricity": measurement["eccentricity"],
        "setup_id": measurement["setup_id"],
        "instrument_type": "SubframeFWHM",
        "instrument_id": measurement["setup_id"],
        "source": "astrocamp_fits",
        # Trailed frames stay in the archive but are flagged out of training:
        # they measure the mount, not the atmosphere.
        "quality_flags": ("trailed" if trailed else "ok")
        + f";stars={measurement['stars']};X={measurement['airmass'] or 0:.2f}"
        + f";filter={measurement['filter']};obj={measurement['object']}",
        "retrieved_at": retrieved_at,
    }


def run(root: Path, site: Site = NERPIO, *, limit: int | None = None,
        pattern: str = "**/*.fit*") -> int:
    retrieved_at = utcnow()
    files = sorted(Path(root).glob(pattern))
    if limit:
        files = files[:limit]

    rows, skipped, trailed = [], 0, 0
    errors: dict[str, int] = {}
    for path in files:
        try:
            measurement = measure_frame(path)
        except Exception as error:
            # A silent except turns a bug into "file skipped" and hides it for
            # weeks. Unreadable frames are expected; anything else is reported.
            errors.setdefault(type(error).__name__, 0)
            errors[type(error).__name__] += 1
            measurement = None
        if not measurement or measurement["t_utc"] is None:
            skipped += 1
            continue
        row = to_row(measurement, site, retrieved_at)
        trailed += row["quality_flags"].startswith("trailed")
        rows.append(row)

    if rows:
        write_canonical(rows, dataset="measurements", site_id=site.site_id)
        values = [r["fwhm_zenith_500nm"] for r in rows
                  if not r["quality_flags"].startswith("trailed")]
        median = float(np.median(values)) if values else float("nan")
        print(f"[fwhm] {len(rows)} frames medidos ({trailed} trazados, "
              f"{skipped} descartados); FWHM mediana a cenit/500nm = {median:.2f}\"")
    else:
        print(f"[fwhm] sin medidas utiles ({skipped} descartados)")
    if errors:
        print(f"[fwhm] errores inesperados: {errors}")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="carpeta con los FITS")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(Path(args.root), limit=args.limit)
