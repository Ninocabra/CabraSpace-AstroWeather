"""The night canvas: the forecast as a picture (SPEC v8 section 1.3).

The canvas IS the night. Time runs left to right from an hour before sunset to
an hour after sunrise, the ridge line sits at the bottom, and everything else
is painted above it in the order the sky actually stacks.

The rule that shapes every choice here: a photorealistic render promises a
precision this data does not have. Models resolve 9-25 km; what we would be
drawing is the structure of the model, not the shape of the clouds. So the
canvas is deliberately diagrammatic, and uncertainty is painted rather than
hidden -- cloud bands lose opacity and gain texture as the ensemble disagrees,
so a 40 % that nobody is sure about cannot look like a confident 40 %.

Safety is never ambiguous: a blocked hour is hatched and locked, whatever else
is going on in that column.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

WIDTH, HEIGHT = 1100, 520
MARGIN_LEFT, MARGIN_RIGHT = 70, 30
HORIZON_Y = 430          # where the ridge line sits
SKY_TOP = 70


@dataclass
class CanvasHour:
    """One column of the canvas."""

    t_utc: dt.datetime
    sky_mag: float                 # background brightness, paints the sky
    cloud_low: float
    cloud_mid: float
    cloud_high: float
    certainty: float               # 0..1, how much the models agree
    usable_probability: float
    moon_altitude: float
    safe: bool
    limiting: str


def _sky_colour(mag: float) -> str:
    """Sky background to colour: 22 is deep black, 18 is a washed-out grey."""
    darkness = max(0.0, min(1.0, (mag - 17.5) / 4.5))
    red = int(8 + (1 - darkness) * 70)
    green = int(10 + (1 - darkness) * 78)
    blue = int(28 + (1 - darkness) * 92)
    return f"rgb({red},{green},{blue})"


def _x(index: int, total: int) -> float:
    span = WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    return MARGIN_LEFT + span * index / max(1, total - 1)


def _ridge(points: int = 60) -> str:
    """Stylised ridge line.

    PLACEHOLDER, and labelled as such on the canvas: the real silhouette comes
    from the 720-bin DEM horizon, which is also what the target engine needs
    for its minimum altitude. Drawing a fake ridge that looks real would be the
    same sin as a photorealistic cloud.
    """
    import math

    coordinates = []
    for i in range(points + 1):
        x = MARGIN_LEFT + (WIDTH - MARGIN_LEFT - MARGIN_RIGHT) * i / points
        wave = (math.sin(i * 0.35) * 12 + math.sin(i * 0.11 + 1.3) * 22
                + math.sin(i * 0.05) * 9)
        coordinates.append(f"{x:.0f},{HORIZON_Y - 18 - wave:.0f}")
    return " ".join(coordinates)


def render(hours: list[CanvasHour], *, title: str, subtitle: str,
           targets: list[tuple[str, float, str]] | None = None) -> str:
    """Build the SVG for one night."""
    total = len(hours)
    step = (WIDTH - MARGIN_LEFT - MARGIN_RIGHT) / max(1, total - 1)
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="system-ui,sans-serif">',
        '<defs>',
        # Hatching for hours where the roof must stay shut.
        '<pattern id="blocked" width="8" height="8" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)">'
        '<rect width="8" height="8" fill="rgba(190,60,60,0.20)"/>'
        '<line x1="0" y1="0" x2="0" y2="8" stroke="rgba(255,120,120,0.55)" '
        'stroke-width="3"/></pattern>',
        # Texture for clouds the models disagree about.
        '<pattern id="uncertain" width="6" height="6" patternUnits="userSpaceOnUse">'
        '<circle cx="1.5" cy="1.5" r="1.1" fill="rgba(255,255,255,0.30)"/></pattern>',
        '</defs>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#0a0c17"/>',
    ]

    # --- the sky itself, one column per hour ---------------------------------
    for index, hour in enumerate(hours):
        x = _x(index, total) - step / 2
        parts.append(f'<rect x="{x:.1f}" y="{SKY_TOP}" width="{step + 1:.1f}" '
                     f'height="{HORIZON_Y - SKY_TOP}" fill="{_sky_colour(hour.sky_mag)}"/>')

    # --- cloud bands: high at the top, low nearest the ridge -----------------
    bands = (("alta", "cloud_high", SKY_TOP + 8, 46, "#dfe4ee"),
             ("media", "cloud_mid", SKY_TOP + 62, 46, "#b9c2d2"),
             ("baja", "cloud_low", SKY_TOP + 116, 46, "#8b95a8"))
    for label, attribute, y, height, colour in bands:
        parts.append(f'<text x="{MARGIN_LEFT - 8}" y="{y + height / 2 + 4}" '
                     f'text-anchor="end" font-size="11" fill="#6d7688">{label}</text>')
        for index, hour in enumerate(hours):
            cover = getattr(hour, attribute) / 100.0
            if cover <= 0.02:
                continue
            x = _x(index, total) - step / 2
            # Opacity carries the cover; the dotted overlay carries the doubt.
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{step + 1:.1f}" '
                         f'height="{height}" fill="{colour}" '
                         f'opacity="{0.15 + 0.75 * cover * hour.certainty:.2f}"/>')
            if hour.certainty < 0.75:
                parts.append(f'<rect x="{x:.1f}" y="{y}" width="{step + 1:.1f}" '
                             f'height="{height}" fill="url(#uncertain)" '
                             f'opacity="{(1 - hour.certainty) * cover:.2f}"/>')

    # --- the Moon's real arc -------------------------------------------------
    arc = [(index, hour) for index, hour in enumerate(hours) if hour.moon_altitude > 0]
    if arc:
        points = " ".join(
            f"{_x(i, total):.0f},"
            f"{HORIZON_Y - (h.moon_altitude / 90) * (HORIZON_Y - SKY_TOP - 20):.0f}"
            for i, h in arc)
        parts.append(f'<polyline points="{points}" fill="none" stroke="#f2e6b8" '
                     f'stroke-width="2" stroke-dasharray="5 4" opacity="0.75"/>')
        mid = arc[len(arc) // 2]
        parts.append(
            f'<circle cx="{_x(mid[0], total):.0f}" '
            f'cy="{HORIZON_Y - (mid[1].moon_altitude / 90) * (HORIZON_Y - SKY_TOP - 20):.0f}" '
            f'r="9" fill="#f5ecc6" opacity="0.9"/>')

    # --- the ridge -----------------------------------------------------------
    parts.append(f'<polygon points="{MARGIN_LEFT},{HORIZON_Y} {_ridge()} '
                 f'{WIDTH - MARGIN_RIGHT},{HORIZON_Y}" fill="#05060c"/>')
    parts.append(f'<text x="{WIDTH - MARGIN_RIGHT}" y="{HORIZON_Y - 4}" '
                 f'text-anchor="end" font-size="9" fill="#3a4152">'
                 f'perfil de sierra PROVISIONAL — pendiente del horizonte real (DEM)</text>')

    # --- usability strip, hour labels and blocking ---------------------------
    strip_y = HORIZON_Y + 12
    for index, hour in enumerate(hours):
        x = _x(index, total) - step / 2
        width = step + 1
        probability = hour.usable_probability
        parts.append(f'<rect x="{x:.1f}" y="{strip_y}" width="{width:.1f}" height="26" '
                     f'fill="rgb({int(40 + 20 * (1 - probability))},'
                     f'{int(70 + 120 * probability)},{int(70 + 60 * probability)})" '
                     f'opacity="0.85"/>')
        if not hour.safe:
            parts.append(f'<rect x="{x:.1f}" y="{SKY_TOP}" width="{width:.1f}" '
                         f'height="{strip_y + 26 - SKY_TOP}" fill="url(#blocked)"/>')
        if index % 2 == 0:
            parts.append(f'<text x="{_x(index, total):.0f}" y="{strip_y + 46}" '
                         f'text-anchor="middle" font-size="10" fill="#7a8296">'
                         f'{hour.t_utc:%H}</text>')

    # --- headline and legend -------------------------------------------------
    parts.append(f'<text x="{MARGIN_LEFT}" y="30" font-size="19" fill="#e8ecf4" '
                 f'font-weight="600">{title}</text>')
    parts.append(f'<text x="{MARGIN_LEFT}" y="52" font-size="13" fill="#8d97ab">'
                 f'{subtitle}</text>')

    if targets:
        parts.append(f'<text x="{MARGIN_LEFT}" y="{HEIGHT - 34}" font-size="12" '
                     f'fill="#9aa4b8">esta noche:</text>')
        for offset, (name, hours_equivalent, note) in enumerate(targets[:3]):
            parts.append(
                f'<text x="{MARGIN_LEFT + 78 + offset * 300}" y="{HEIGHT - 34}" '
                f'font-size="12" fill="#dfe5f0">{name} '
                f'<tspan fill="#8fd1a0">{hours_equivalent:.1f} h eq</tspan></text>')
            parts.append(
                f'<text x="{MARGIN_LEFT + 78 + offset * 300}" y="{HEIGHT - 18}" '
                f'font-size="10" fill="#6d7688">{note}</text>')

    parts.append('</svg>')
    return "\n".join(parts)
