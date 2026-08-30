"""What is worth photographing tonight, and how much it will yield.

The product this whole system was built for (SPEC v8 section 1.1). Almost every
astronomy weather service answers "how will the sky be". This one answers "what
should I point at, and what will I actually get", which is a different question
and a much more useful one.

Two numbers per target, and both are published because they mean different
things:

    Q_cond  hours of reference-quality integration IF the sky cooperates
    E[Q]    the same, multiplied by the odds that it does

A target with a magnificent 3 hours behind a 20 % chance of clear sky is not
the same proposition as a modest 2 hours behind 90 %, and collapsing them into
one figure hides exactly the trade-off the observer is making.

The per-hour yield weighs three penalties against a reference night:

    extinction   10^(-0.8 k (X-1))          light lost on the way in
    background   B_ref / B_sky              faint targets drown in bright sky
    seeing       (FWHM_ref / FWHM)^(2 alpha) only compact targets care

alpha is what stops the engine treating everything alike: 0 for extended
nebulosity, whose surface brightness does not care about blur; 1 for compact
sources, where the noise in the aperture grows as FWHM^2 and a 1.5 vs 3 arcsec
night is a factor four in speed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
from astropy.coordinates import AltAz, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u

from .ephem import location, moon_phase_angle, moon_positions, night_for
from .extinction import evaluate as evaluate_transparency
from .moonsky import mag_to_nl, moon_brightness, nl_to_mag
from .seeing import Turbulence
from .site import NERPIO, Site
from .skybackground import epoch_floor, natural_background
from .skymodel import predict

# Reference night: the yield of one hour under these conditions is 1.0.
FWHM_REFERENCE = 2.0
K_REFERENCE = 0.18
# Below this altitude nothing is worth starting; extinction and differential
# refraction grow faster than the target rises.
MIN_ALTITUDE = 25.0
# Width of the transition around each class gate, and the most a good seeing
# night is allowed to be worth. Both exist because an uncalibrated model must
# not be able to produce a spectacular number (see hourly_yield).
GATE_SHOULDER_MAG = 0.4
MAX_SHARPNESS_GAIN = 2.0


def _shoulder(value: float, centre: float, width: float) -> float:
    """Smooth 0->1 ramp across `width`, reaching 0.5 exactly at `centre`."""
    if width <= 0:
        return 1.0 if value >= centre else 0.0
    t = (value - (centre - width / 2)) / width
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)          # smoothstep: flat at both ends

# Sky brightness each class of target NEEDS, in mag/arcsec^2 at its position.
# The continuous yield alone is not enough: it would happily offer four tenths
# of an hour of IFN under a full Moon, and there is no such thing. These are
# the sanity cuts of SPEC v8 section 1.2 -- an hour brighter than the class
# threshold counts as zero for that target, not as a little bit of progress.
# Fraccion de la luz que sobrevive a cada filtro. Son los valores de respaldo
# que SPEC v8 seccion 2.3 fija, etiquetados approximation_quality=low hasta que
# la LUT de SkyCalc los sustituya: el factor real depende de fase, forma del
# filtro y QE.
#
# Hasta el 2026-08-28 el codigo NO usaba estos: tenia un unico 0,12 por clase
# que no esta en SPEC. Mientras el cielo con Luna salia 1,8 mag mas oscuro de lo
# que es, el 0,12 colaba; con la ley de dispersion lunar medida
# (moonscatter.py), el producto pasaba a decir que con Luna llena no se hace
# banda estrecha. Como consejo es falso, y ademas contradice al documento
# normativo, que manda.
NARROWBAND_FILTERS = {
    "Ha 3nm": 0.05,
    "SII": 0.08,
    "OIII": 0.20,
}
# La clase "narrowband" se puntua con el filtro MAS tolerante a la Luna, porque
# la pregunta que responde el gate es "se puede observar", y con Luna llena lo
# que se puede es Ha. El veredicto dice de que filtro habla: un booleano solo
# seria mas corto, no mas cierto -- OIII con Luna llena no cuaja.
NARROWBAND_REFERENCE = "Ha 3nm"
MOON_FILTER_FACTOR = {
    "narrowband": NARROWBAND_FILTERS[NARROWBAND_REFERENCE],
    "compacto_resolucion": 1.0,
    "extremo_contraste": 1.0,
    "bajo_contraste": 1.0,
    "normal": 1.0,
}

SKY_GATE = {
    "extremo_contraste": 21.3,     # IFN, tidal tails, haloes
    "bajo_contraste": 20.9,        # faint broadband structure
    "normal": 20.0,
    "compacto_resolucion": 19.5,   # small and bright: tolerates sky, not blur
    "narrowband": 19.5,            # applied AFTER the filter factor above
}


@dataclass(frozen=True)
class Target:
    """A target with the two things that decide how it behaves tonight."""

    name: str
    ra_deg: float
    dec_deg: float
    kind: str
    # Morphology exponent: 0 extended, 0.5 intermediate, 1 compact.
    alpha: float
    # Curated difficulty, because a catalogue surface brightness is not
    # comparable across object classes (SPEC v8 section 1.1).
    difficulty: str
    note: str = ""
    # Otros nombres por los que alguien lo buscaria: el ingles, la designacion
    # alternativa, el numero de otro catalogo. La pagina en ingles buscaba
    # "Veil" y no encontraba el Velo (29-08-2026): un buscador que solo conoce
    # un idioma es medio buscador.
    aliases: tuple[str, ...] = ()

    def positions(self, times: list[dt.datetime], site: Site = NERPIO,
                  luna: tuple[np.ndarray, np.ndarray] | None = None):
        """Altura, azimut y separacion a la Luna de este objeto, muestra a muestra.

        `luna` son las alturas y azimuts de la Luna en esos MISMOS instantes,
        que quien puntua una noche entera ya tiene calculados. Pasarlos ahorra
        recalcular la Luna una vez por objeto: con 983 objetos eran 17 de los
        50 segundos de una exportacion, y la Luna es la misma para todos.
        """
        when = Time([t.astimezone(dt.timezone.utc) for t in times])
        frame = AltAz(obstime=when, location=location(site))
        coordinates = SkyCoord(ra=self.ra_deg * u.deg, dec=self.dec_deg * u.deg)
        altaz = coordinates.transform_to(frame)
        # LA SEPARACION SE MIDE EN EL MISMO MARCO QUE LA ALTURA, y hasta el
        # 30-08-2026 no era asi: era `coordinates.separation(get_body("moon"))`,
        # o sea un objeto en ICRS -- que es BARICENTRICO -- contra la Luna en
        # GCRS, que es geocentrico. Entre esos dos marcos hay una TRASLACION de
        # una unidad astronomica, no un giro, y astropy lo avisaba en cada
        # llamada ("angular separation can depend on the direction of the
        # transformation"); el aviso se leia como ruido de astropy y era el
        # numero saliendo mal. La Luna acababa colocada donde se la ve desde el
        # BARICENTRO del sistema solar en vez de desde aqui.
        #
        # No es un decimal: para NGC 7635 la noche del 30-08-2026 daba 70,7
        # grados donde la Luna estaba a 55,7. Y con la ley de dispersion medida
        # aqui -- 10^(-theta/48,55) -- quince grados son un factor DOS en el
        # brillo lunar sobre el objeto, que entra en su rendimiento y en su
        # veredicto. Verificado contra las tres formas honestas de calcularlo
        # (los dos en AltAz, los dos en GCRS, y a mano sobre la AR/Dec aparente
        # de la Luna): las tres dan 55,74.
        #
        # No toca la ley lunar: esa se ajusto con el fotometro cenital, donde
        # theta es la distancia cenital de la Luna (90 - altura) y no pasa por
        # aqui. Y ahora la separacion del motor y la que dibuja la cupula en el
        # navegador son la misma cuenta, que era la otra mitad del problema.
        alturas = np.asarray(altaz.alt.deg, dtype=float)
        azimuts = np.asarray(altaz.az.deg, dtype=float)
        if luna is None:
            posicion = get_body("moon", when, location=location(site)).transform_to(frame)
            luna = (np.asarray(posicion.alt.deg, dtype=float),
                    np.asarray(posicion.az.deg, dtype=float))
        return alturas, azimuts, separacion_angular(alturas, azimuts, *luna)


def separacion_angular(alt1, az1, alt2, az2):
    """Separacion sobre la esfera, en grados, desde alturas y azimuts.

    La misma cuenta que hace la cupula del navegador, a proposito: es la unica
    forma de que el motor y el dibujo no puedan discrepar, y discrepaban en 38
    grados hasta el 30-08-2026 por comparar marcos con una traslacion de una
    unidad astronomica entre medias.
    """
    a1, z1, a2, z2 = (np.radians(np.asarray(x, dtype=float))
                      for x in (alt1, az1, alt2, az2))
    coseno = np.sin(a1) * np.sin(a2) + np.cos(a1) * np.cos(a2) * np.cos(z1 - z2)
    return np.degrees(np.arccos(np.clip(coseno, -1.0, 1.0)))


# A curated list. Fifty well-understood objects beat twenty thousand with
# incomparable photometry, which is why this is hand-written: `difficulty` and
# `alpha` son juicios sobre el objeto que ningun catalogo trae, y sin ellos el
# recomendador no puede decir nada util.
#
# Ampliada el 29-08-2026 de 10 a 44 para que el buscador de la web tenga a que
# buscar. Las coordenadas de los nuevos NO se escribieron de memoria: se
# resolvieron contra SIMBAD (`SkyCoord.from_name`) y se pegaron. Las de los diez
# originales se dejan como estaban -- en objetos extensos el "centro" es una
# decision de encuadre, no un dato, y la de un catalogo no tiene por que ser
# mejor que la que ya se habia elegido a mano.
CATALOGUE = [
    Target("IFN en M81/M82", 148.9, 69.1, "ifn", 0.0, "extremo_contraste",
           "el target mas exigente en contraste que existe: sin Luna o nada",
           aliases=("IFN", "Integrated Flux Nebula", "cirros galacticos",)),
    Target("M51 (halo y puente de marea)", 202.47, 47.19, "galaxy", 0.5,
           "bajo_contraste", "el nucleo sale casi siempre; el puente, no",
           aliases=("Whirlpool", "Remolino", "NGC 5194",)),
    Target("M31", 10.68, 41.27, "galaxy", 0.5, "normal", "",
           aliases=("Andromeda", "Andromeda Galaxy", "NGC 224",)),
    Target("M81", 148.89, 69.07, "galaxy", 0.5, "normal", "",
           aliases=("Bode", "Bode's Galaxy", "NGC 3031",)),
    Target("NGC 7000 (Norteamérica)", 314.75, 44.52, "nebula", 0.0, "narrowband",
           "banda estrecha: aprovecha noches de Luna",
           aliases=("North America Nebula", "Norteamerica", "Caldwell 20",)),
    Target("Velo (NGC 6960)", 311.66, 30.72, "nebula", 0.0, "narrowband", "",
           aliases=("Veil", "Veil Nebula", "Witch's Broom", "Escoba de Bruja",)),
    Target("M57 (Anillo)", 283.40, 33.03, "planetary", 1.0, "compacto_resolucion",
           "pequena: el seeing manda",
           aliases=("Ring Nebula", "Anillo", "NGC 6720",)),
    Target("Cruz de Einstein (Q2237+0305)", 340.13, 3.36, "lens", 1.0,
           "compacto_resolucion",
           "componentes a 1.8 arcsec: solo con seeing excepcional",
           aliases=("Einstein Cross", "Huchra's Lens", "Q2237",)),
    Target("M13", 250.42, 36.46, "cluster", 0.5, "normal",
           "poco exigente: buen plan de noche mediocre",
           aliases=("Hercules Cluster", "Great Globular", "NGC 6205",)),
    Target("M27 (Dumbbell)", 299.90, 22.72, "planetary", 0.5, "normal", "",
           aliases=("Dumbbell Nebula", "Pesa", "NGC 6853",)),
    Target("M1 (Cangrejo)", 83.6324, 22.0174, "nebula", 0.5, "narrowband",
           "resto de supernova: Halfa y OIII",
           aliases=("Crab Nebula", "Crab", "NGC 1952",)),
    Target("M8 (Laguna)", 270.9042, -24.3867, "nebula", 0.0, "narrowband", "",
           aliases=("Lagoon Nebula", "Lagoon", "NGC 6523",)),
    Target("M16 (Aguila)", 274.6880, -13.7920, "nebula", 0.0, "narrowband",
           "los Pilares piden focal larga",
           aliases=("Eagle Nebula", "Pillars of Creation", "NGC 6611",)),
    Target("M17 (Omega)", 275.1958, -16.1717, "nebula", 0.0, "narrowband", "",
           aliases=("Omega Nebula", "Swan Nebula", "NGC 6618",)),
    Target("M20 (Trifida)", 270.6750, -22.9717, "nebula", 0.0, "normal",
           "mezcla emision y reflexion: no es solo banda estrecha",
           aliases=("Trifid Nebula", "Trifid", "NGC 6514",)),
    Target("M33 (Triangulo)", 23.4621, 30.6602, "galaxy", 0.5, "bajo_contraste",
           "brillo superficial bajo: enganosa",
           aliases=("Triangulum Galaxy", "Pinwheel", "NGC 598",)),
    Target("M42 (Orion)", 83.8201, -5.3876, "nebula", 0.0, "normal",
           "nucleo saturado: exposiciones cortas aparte",
           aliases=("Orion Nebula", "Great Orion Nebula", "NGC 1976",)),
    Target("M45 (Pleyades)", 56.6008, 24.1139, "cluster", 0.5, "bajo_contraste",
           "la nebulosa de reflexion es lo dificil",
           aliases=("Pleiades", "Seven Sisters", "Siete Cabrillas",)),
    Target("M63 (Girasol)", 198.9553, 42.0294, "galaxy", 0.5, "bajo_contraste", "",
           aliases=("Sunflower Galaxy", "NGC 5055",)),
    Target("M64 (Ojo Negro)", 194.1821, 21.6827, "galaxy", 0.5, "normal", "",
           aliases=("Black Eye Galaxy", "NGC 4826",)),
    Target("M65", 169.7330, 13.0923, "galaxy", 0.5, "normal", "",
           aliases=("NGC 3623",)),
    Target("M66", 170.0626, 12.9915, "galaxy", 0.5, "normal", "",
           aliases=("NGC 3627",)),
    Target("M78", 86.6908, 0.0792, "nebula", 0.5, "bajo_contraste",
           "reflexion: nada de banda estrecha",
           aliases=("NGC 2068",)),
    Target("M82 (Cigarro)", 148.9685, 69.6797, "galaxy", 0.5, "normal",
           "los chorros de Halfa piden filtro",
           aliases=("Cigar Galaxy", "NGC 3034",)),
    Target("M92", 259.2808, 43.1359, "cluster", 0.5, "normal", "",
           aliases=("NGC 6341",)),
    Target("M97 (Lechuza)", 168.6988, 55.0190, "planetary", 1.0, "compacto_resolucion", "",
           aliases=("Owl Nebula", "NGC 3587",)),
    Target("M101 (Molinete)", 210.8024, 54.3488, "galaxy", 0.5, "bajo_contraste",
           "brillo superficial bajo",
           aliases=("Pinwheel Galaxy", "NGC 5457",)),
    Target("M104 (Sombrero)", 189.9976, -11.6231, "galaxy", 0.5, "normal", "",
           aliases=("Sombrero Galaxy", "NGC 4594",)),
    Target("M106", 184.7401, 47.3037, "galaxy", 0.5, "normal", "",
           aliases=("NGC 4258",)),
    Target("M15", 322.4930, 12.1670, "cluster", 0.5, "normal", "",
           aliases=("NGC 7078",)),
    Target("M22", 279.0997, -23.9047, "cluster", 0.5, "normal", "",
           aliases=("NGC 6656",)),
    Target("NGC 6888 (Creciente)", 303.0273, 38.3549, "nebula", 0.0, "narrowband", "",
           aliases=("Crescent Nebula", "Caldwell 27",)),
    Target("NGC 7023 (Iris)", 315.4038, 68.1633, "nebula", 0.5, "bajo_contraste",
           "reflexion mas polvo: banda ancha y cielo oscuro",
           aliases=("Iris Nebula", "Caldwell 4",)),
    Target("NGC 7635 (Burbuja)", 350.2013, 61.2017, "nebula", 0.0, "narrowband", "",
           aliases=("Bubble Nebula", "Caldwell 11",)),
    Target("NGC 7293 (Helix)", 337.4106, -20.8372, "planetary", 0.5, "narrowband",
           "baja desde Nerpio: mira la altura",
           aliases=("Helix Nebula", "Ojo de Dios", "Caldwell 63",)),
    Target("NGC 2237 (Roseta)", 97.6504, 4.9807, "nebula", 0.0, "narrowband", "",
           aliases=("Rosette Nebula", "Caldwell 49",)),
    Target("NGC 253", 11.8881, -25.2888, "galaxy", 0.5, "normal", "",
           aliases=("Sculptor Galaxy", "Caldwell 65",)),
    Target("NGC 891", 35.6371, 42.3483, "galaxy", 0.5, "bajo_contraste",
           "canal de polvo: pide resolucion y cielo",
           aliases=("Caldwell 23",)),
    Target("NGC 2903", 143.0421, 21.5008, "galaxy", 0.5, "normal", "",
           aliases=("Caldwell 39",)),
    Target("NGC 4565 (Aguja)", 189.0867, 25.9874, "galaxy", 0.5, "normal", "",
           aliases=("Needle Galaxy", "Caldwell 38",)),
    Target("IC 434 (Cabeza de Caballo)", 85.2500, -2.5000, "nebula", 0.0, "narrowband", "",
           aliases=("Horsehead Nebula", "Barnard 33",)),
    Target("IC 1396 (Trompa del Elefante)", 324.7450, 57.5139, "nebula", 0.0, "narrowband", "",
           aliases=("Elephant's Trunk Nebula", "Trompa",)),
    Target("IC 1805 (Corazon)", 38.2100, 61.4711, "nebula", 0.0, "narrowband", "",
           aliases=("Heart Nebula", "Corazon", "Melotte 15",)),
    Target("IC 1848 (Alma)", 42.8550, 60.4189, "nebula", 0.0, "narrowband", "",
           aliases=("Soul Nebula", "Alma", "Westerhout 5",)),
]


@dataclass
class TargetNight:
    """What one target yields over one night."""

    target: Target
    q_conditional: float
    q_expected: float
    hours_above_minimum: float
    max_altitude: float
    min_moon_separation: float
    best_hour: dt.datetime | None
    reason: str
    # Hour by hour, what the engine actually saw. Exposed so the charts read
    # the SAME numbers the score came from: a view that recomputes them is a
    # view that will one day disagree with the recommendation it illustrates.
    trace: list[dict] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.hours_above_minimum == 0:
            return "no observable"
        if self.q_expected >= 1.5:
            return "recomendado"
        if self.q_expected >= 0.5:
            return "posible"
        return "poco rentable"


def hourly_yield(*, airmass: float, sky_mag: float, seeing_arcsec: float,
                 k_total: float, alpha: float, difficulty: str = "normal") -> float:
    """Fraction of a reference hour that this hour is worth for this target.

    Zero when the sky is brighter than the class can survive: partial credit
    for an impossible hour is worse than no credit, because it accumulates.
    """
    if airmass is None or airmass <= 0:
        return 0.0
    gate = SKY_GATE.get(difficulty, 20.0)
    # The gate used to be a step, and a step is a lie: an hour at 21.28 scored
    # zero while 21.30 scored 0.685 (external audit, 2026-08-27). Signal-to-
    # noise degrades smoothly, so the gate becomes a shoulder and keeps its
    # meaning at the 50 % point. The width is the scale of the yield function
    # itself: 0.4 mag is a factor 1.45 in background flux.
    admissible = _shoulder(sky_mag, gate, GATE_SHOULDER_MAG)
    if admissible <= 0.0:
        return 0.0
    extinction = 10 ** (-0.8 * k_total * (airmass - 1.0))
    # Contra el suelo de la EPOCA, no contra una constante de siete anios:
    # el cielo de aqui se ha oscurecido 0,56 mag desde 2019 y ese error
    # entraba en el rendimiento de cada hora.
    background = min(1.0, 10 ** (0.4 * (sky_mag - epoch_floor())))
    if alpha > 0:
        sharpness = (FWHM_REFERENCE / max(seeing_arcsec, 0.3)) ** (2 * alpha)
    else:
        sharpness = 1.0                 # extended targets do not care about blur
    return float(extinction * background * admissible
                 * min(sharpness, MAX_SHARPNESS_GAIN))


def _sample_weights(times: list[dt.datetime]) -> list[float]:
    """Hours each sample stands for, read off the grid instead of assumed.

    This used to be `times[1] - times[0]`, applied to every sample of the
    night. On a uniform grid that is a fencepost error -- n samples of one hour
    each cover n hours where the night only spans n-1 -- and on the ADAPTIVE
    grid it is a factor: `adaptive_hours` opens in twilight at 30 minutes and
    then switches to 10, so every dark sample was counted at triple weight and
    the night came out 2.26x too long (58 samples, 12.83 h of span, 29.00 h
    scored; measured 2026-08-29).

    The trapezoid rule fixes both at once: each sample takes half the distance
    to each neighbour, the ends take their single half, and the weights sum to
    exactly the span of the grid whatever the spacing. Yield is a smooth
    function of time, so there is no reason to integrate it any other way.
    """
    if len(times) < 2:
        # Not a night, and nothing builds one: keep the old one-hour
        # convention rather than silently scoring a degenerate grid at zero.
        return [1.0] * len(times)
    return [(times[min(index + 1, len(times) - 1)]
             - times[max(index - 1, 0)]).total_seconds() / 7200
            for index in range(len(times))]


def evaluate_night(targets: list[Target], times: list[dt.datetime],
                   *, usable_probability: list[float],
                   turbulence: Turbulence | None = None,
                   aod_total: float | None = None, aod_dust: float | None = None,
                   site: Site = NERPIO) -> list[TargetNight]:
    """Score every target over the night, using the whole physics stack."""
    transparency = evaluate_transparency(
        aod_total=aod_total, aod_dust=aod_dust,
        elevation_m=site.site_elevation, delta_h_m=site.delta_h)
    # The floor must carry twilight, not just the natural background: the
    # product window opens an hour before sunset, and without the twilight term
    # those daylight hours look like pristine dark sky and score as usable.
    floors = {c.t_utc: c.predicted_mag for c in predict(times, site)}
    # ...and the NATURAL part of that zenith floor on its own, so it can be
    # swapped for the one where each target actually is. Without this the whole
    # dome was scored with the zenith's Milky Way, which is wrong by up to
    # 0.405 mag (skybackground.natural_background).
    zenith_natural = natural_background(times, np.full(len(times), 90.0),
                                        np.zeros(len(times)), site)
    zenith_natural_flux = {t: mag_to_nl(v) for t, v in zip(times, zenith_natural)}
    moon = {p.t_utc: p for p in moon_positions(times, site)}
    phase = moon_phase_angle(times[len(times) // 2])
    # How much of the night each sample speaks for. Depends only on the grid,
    # so it is computed once and shared by every target.
    weights = _sample_weights(times)

    # La Luna, UNA vez para toda la noche y para todos los objetos. Antes se
    # recalculaba dentro de `positions()` en cada uno de los 983 objetivos.
    luna_altaz = (np.array([moon[t].altitude for t in times], dtype=float),
                  np.array([moon[t].azimuth for t in times], dtype=float))

    results = []
    for target in targets:
        altitudes, azimuths, separations = target.positions(times, site, luna_altaz)
        # The natural background along THIS target's own track: one coordinate
        # transform per target, vectorised over the night. The field is smooth
        # (26.5 deg of scale) so no grid is needed -- it is cheaper and exact to
        # evaluate the analytic term where the telescope is actually pointing.
        target_natural_flux = np.array(
            [mag_to_nl(v) for v in natural_background(times, altitudes,
                                                      azimuths, site)])
        conditional, expected, hours, best, best_value = 0.0, 0.0, 0.0, None, 0.0

        trace: list[dict] = []
        for index, when in enumerate(times):
            altitude = float(altitudes[index])
            if altitude < MIN_ALTITUDE:
                trace.append({"alt": round(altitude, 1),
                              "az": round(float(azimuths[index]), 1),
                              "yield": 0.0, "sky_mag": None,
                              "moon_sep": round(float(separations[index]), 1),
                              "gate_ok": False, "too_low": True,
                              "limiting": "altura",
                              "why": f"a {altitude:.0f} grados no llega al minimo "
                                     f"de {MIN_ALTITUDE:.0f}"})
                continue
            airmass = 1.0 / np.sin(np.radians(altitude))
            hours += weights[index]

            # predict() already carries the Moon AT THE ZENITH; here we need it
            # at the TARGET's real separation, which is the whole point of the
            # directional model. So the zenith moonlight is removed and the
            # target's own contribution added in its place.
            zenith_moon = moon_brightness(
                phase_angle_deg=phase,
                separation_deg=max(1.0, 90.0 - moon[when].altitude),
                moon_altitude_deg=moon[when].altitude,
                target_altitude_deg=90.0,
                extinction_k=transparency.k_total,
            )
            target_moon = moon_brightness(
                phase_angle_deg=phase,
                separation_deg=float(separations[index]),
                moon_altitude_deg=moon[when].altitude,
                target_altitude_deg=altitude,
                extinction_k=transparency.k_total,
            )
            # The filter cuts EVERYTHING outside its passband, not just the
            # Moon: twilight, airglow and zodiacal light are broadband too.
            # Attenuating only the lunar term (as this did until the audit of
            # 2026-08-27) left narrowband facing an unfiltered sky and zeroed
            # it through both twilights -- backwards, since twilight narrowband
            # is precisely what the technique is good at.
            filter_factor = MOON_FILTER_FACTOR.get(target.difficulty, 1.0)
            # Take the zenith total apart and put the target's own terms back:
            # out goes the zenith Moon and the zenith natural background, in
            # come the target's. What survives the subtraction is twilight,
            # which this model has no azimuth dependence for anyway
            # (skymodel.twilight_magnitude), so it is right to carry it across.
            flux = max(mag_to_nl(floors[when])
                       - zenith_moon.physical_nl
                       - zenith_natural_flux[when], 1e-6)
            flux += target_natural_flux[index] + target_moon.physical_nl
            sky_mag = nl_to_mag(flux * filter_factor)

            # The seeing model is uncalibrated and known to run OPTIMISTIC
            # (1.21 predicted where 3.16 was measured), so the yield is taken
            # from the pessimistic edge of the published band, never from the
            # point value. Using the loose number contradicted our own rule of
            # publishing terciles, and swung compact targets by a factor 3.7.
            seeing = ((turbulence.range_arcsec[1] * airmass ** 0.6) if turbulence
                      else FWHM_REFERENCE * airmass ** 0.6)
            value = hourly_yield(airmass=airmass, sky_mag=sky_mag,
                                 seeing_arcsec=seeing,
                                 k_total=transparency.k_total, alpha=target.alpha,
                                 difficulty=target.difficulty)
            conditional += value * weights[index]
            expected += value * weights[index] * usable_probability[index]
            if value > best_value:
                best_value, best = value, when
            # Which of the three penalties is actually costing this hour.
            # The recommendation is worth little if it cannot say why.
            gate = SKY_GATE.get(target.difficulty, 20.0)
            extinction_loss = 1 - 10 ** (-0.8 * transparency.k_total * (airmass - 1))
            background_loss = 1 - min(1.0, 10 ** (0.4 * (sky_mag - epoch_floor())))
            moon_up = moon[when].altitude > 0
            if value <= 0.0:
                limiting = "cielo"
                why = (f"cielo a {sky_mag:.1f}, por debajo del corte de "
                       f"{gate:.1f} de su clase"
                       + (f" (medido en {NARROWBAND_REFERENCE}; con OIII sale "
                          f"peor)" if target.difficulty == "narrowband" else "")
                       + (f"; la Luna esta a {separations[index]:.0f} grados"
                          if moon_up else ""))
            # A moonless sky sitting at its own natural floor is not a
            # "limiting factor": reporting 20 % lost on the best night of the
            # year is true against a bare-floor reference and useless to act
            # on. It only counts as limiting when something PUT it there.
            elif (background_loss > extinction_loss
                  and background_loss > (0.12 if moon_up else 0.38)):
                limiting = "luna" if moon_up else "fondo"
                why = (f"el fondo a {sky_mag:.1f} le quita "
                       f"{background_loss*100:.0f} % del rendimiento"
                       + (f", con la Luna a {separations[index]:.0f} grados"
                          if moon_up else " (crepusculo)"))
            elif extinction_loss > 0.12:
                limiting = "altura"
                why = (f"a {altitude:.0f} grados la masa de aire {airmass:.2f} "
                       f"se lleva {extinction_loss*100:.0f} %")
            else:
                limiting = "ninguno"
                why = (f"hora limpia: cielo natural {sky_mag:.1f} a "
                       f"{altitude:.0f} grados, nada la estorba")

            trace.append({"alt": round(altitude, 1),
                          "az": round(float(azimuths[index]), 1),
                          "yield": round(value, 3),
                          "sky_mag": round(sky_mag, 2),
                          "moon_sep": round(float(separations[index]), 1),
                          "gate_ok": value > 0.0, "too_low": False,
                          "limiting": limiting, "why": why})

        results.append(TargetNight(
            target=target,
            q_conditional=conditional,
            q_expected=expected,
            hours_above_minimum=hours,
            max_altitude=float(altitudes.max()),
            min_moon_separation=float(separations.min()),
            best_hour=best,
            reason=_reason(target, conditional, hours, float(separations.min()),
                           transparency, turbulence),
            trace=trace,
        ))
    return sorted(results, key=lambda r: r.q_expected, reverse=True)


def _reason(target: Target, conditional: float, hours: float,
            moon_separation: float, transparency, turbulence) -> str:
    """Every verdict carries its why, in one sentence."""
    if hours == 0:
        return f"no sube de {MIN_ALTITUDE:.0f} grados esta noche"
    if moon_separation < 30 and target.difficulty == "extremo_contraste":
        return f"la Luna pasa a {moon_separation:.0f} grados: imposible para SB extremo"
    if transparency.dust_alert and target.alpha < 0.5:
        return "calima: los objetos extensos y débiles no cuajan"
    if target.alpha >= 1.0 and turbulence and turbulence.tercile == "desfavorable":
        return f"seeing {turbulence.tercile}: los detalles finos se pierden"
    if conditional <= 0.01:
        gate = SKY_GATE.get(target.difficulty, 20.0)
        return (f"el cielo no baja de {gate:.1f} mag/arcsec2 esta noche: "
                f"esta clase no cuaja")
    if conditional < 0.3:
        return "poca altura o cielo demasiado brillante para rendir"
    return f"{conditional:.1f} h equivalentes si despeja"
