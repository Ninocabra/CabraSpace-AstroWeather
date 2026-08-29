"""Que hacer ESTA noche: la base de consejos de adquisicion.

El producto ya decia como esta la noche y que objetos rinden. Lo que faltaba es
lo que un astrofotografo con experiencia te diria al oido: "con esa Luna no
saques RGB", "hoy Halfa si, OIII no", "el seeing no da para la planetaria",
"cierra antes de que condense". Eso es lo que hay aqui.

COMO ESTA CONSTRUIDO, y por que asi:

Cada consejo es una REGLA con su condicion escrita en numeros, no una frase
suelta. La condicion se evalua contra la noche real y el consejo sale con los
numeros que lo disparan dentro. Un consejo sin su porque no vale nada: quien lo
lee tiene que poder discrepar, y para discrepar necesita ver la cuenta.

Las reglas cruzan dos ejes:

* **Que quieres adquirir**: RGB, luminancia, Halfa, OIII/SII, dual-band,
  planetaria y lunar, y campo estelar.
* **Que va mal esta noche**: Luna (brillo y separacion), transparencia y
  calima, seeing, nubes, humedad y rocio, viento, y crepusculo.

No todas las combinaciones existen, y eso es informacion: la Luna llena arruina
el RGB profundo y no toca la planetaria; la calima mata el OIII y deja pasar el
Halfa; el seeing malo hunde la planetaria y no molesta al IFN. La tabla dice
para quien es cada aviso, y el que no aplica no se publica.

CONTRA QUE SE CONTRASTA CADA UMBRAL:

Los cortes por filtro salen de SPEC 2.3, los de cielo de `targets.SKY_GATE`, y
los de seeing de los terciles de `seeing.py`. No se inventa ninguno aqui: si un
numero cambia alli, cambia el consejo, y no hay dos verdades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .targets import MOON_FILTER_FACTOR, SKY_GATE

# Cortes de la Luna, en fraccion iluminada. No son gustos: son los puntos donde
# el fondo de cielo cruza las puertas de `SKY_GATE` para banda ancha en un sitio
# de 21,7 mag naturales.
MOON_LOW = 0.25
MOON_MID = 0.55
MOON_HIGH = 0.80

# Separacion a la Luna por debajo de la cual la aureola manda sobre todo lo
# demas (K&S: la rama rho<10 grados va como rho^-2).
MOON_CLOSE_DEG = 30.0

SEVERITIES = ("evitar", "cuidado", "aprovecha")


@dataclass(frozen=True)
class Advice:
    """Un consejo, con a quien va dirigido y por que se dispara."""

    key: str
    technique: str          # 'rgb' | 'luminancia' | 'halfa' | 'oiii' | ...
    severity: str           # 'evitar' | 'cuidado' | 'aprovecha'
    problem: str            # 'luna' | 'calima' | 'seeing' | 'nubes' | ...
    title: str
    body: str
    when: Callable[["Conditions"], bool] = field(compare=False, default=lambda c: False)

    def render(self, conditions: "Conditions") -> dict:
        return {
            "clave": self.key,
            "tecnica": self.technique,
            "problema": self.problem,
            "severidad": self.severity,
            "titulo": self.title,
            "texto": self.body.format(**conditions.as_format()),
        }


@dataclass
class Conditions:
    """Lo que la noche tiene, ya resumido. Todo opcional: lo que no se sabe,
    no dispara consejos -- callarse es mejor que adivinar."""

    moon_illumination: float = 0.0
    moon_altitude_max: float | None = None
    sky_mag: float | None = None
    seeing_tercile: str | None = None
    seeing_range: tuple[float, float] | None = None
    aod: float | None = None
    k_total: float | None = None
    dust_alert: bool = False
    transparency_verdict: str | None = None
    open_probability: float | None = None
    dark_hours: float = 0.0
    humidity: float | None = None
    dew_margin: float | None = None      # t2m - dew_point, en grados
    wind: float | None = None
    wind_uncalibrated: bool = False

    def as_format(self) -> dict:
        """Los numeros listos para meter en el texto, ya redondeados."""
        return {
            "luna_pct": f"{self.moon_illumination * 100:.0f}",
            "luna_alt": f"{self.moon_altitude_max:.0f}" if self.moon_altitude_max is not None else "?",
            "cielo": f"{self.sky_mag:.1f}" if self.sky_mag is not None else "?",
            "seeing": self.seeing_tercile or "?",
            "seeing_alto": (f"{self.seeing_range[1]:.1f}" if self.seeing_range else "?"),
            "aod": f"{self.aod:.3f}" if self.aod is not None else "?",
            "k": f"{self.k_total:.2f}" if self.k_total is not None else "?",
            "p_abrir": f"{self.open_probability * 100:.0f}" if self.open_probability is not None else "?",
            "oscuridad": f"{self.dark_hours:.1f}",
            "humedad": f"{self.humidity:.0f}" if self.humidity is not None else "?",
            "margen_rocio": f"{self.dew_margin:.1f}" if self.dew_margin is not None else "?",
            "viento": f"{self.wind * 3.6:.0f}" if self.wind is not None else "?",
            "gate_narrow": f"{SKY_GATE['narrowband']:.1f}",
            "gate_normal": f"{SKY_GATE['normal']:.1f}",
            "gate_extremo": f"{SKY_GATE['extremo_contraste']:.1f}",
            "factor_ha": f"{MOON_FILTER_FACTOR.get('narrowband', 1.0):.2f}",
        }


def _luna(c: Conditions) -> float:
    return c.moon_illumination


RULES: list[Advice] = [
    # ---------------------------------------------------------------- LUNA --
    Advice(
        key="rgb_luna_alta", technique="rgb", problem="luna", severity="evitar",
        title="Hoy no saques RGB de campo profundo",
        body="La Luna está al {luna_pct} % y el fondo de cielo se queda en "
             "{cielo} mag/arcsec², por encima de la puerta de {gate_normal} que "
             "necesita la banda ancha. El ruido de fondo se come el color antes "
             "de que el objeto suba. La señal que pierdes esta noche no la "
             "recuperas apilando: apilar baja el ruido, no el fondo.",
        when=lambda c: _luna(c) >= MOON_HIGH,
    ),
    Advice(
        key="rgb_luna_media", technique="rgb", problem="luna", severity="cuidado",
        title="RGB solo en objetos brillantes",
        body="Con la Luna al {luna_pct} % el color sigue saliendo en cúmulos y "
             "galaxias brillantes, pero olvídate de las partes débiles: los "
             "brazos exteriores y el halo se quedan dentro del ruido del fondo.",
        when=lambda c: MOON_MID <= _luna(c) < MOON_HIGH,
    ),
    Advice(
        key="halfa_luna", technique="halfa", problem="luna", severity="aprovecha",
        title="Noche de Halfa: aprovéchala",
        body="La Luna al {luna_pct} % es justo la noche que el Halfa está hecho "
             "para robar. El filtro deja pasar un factor {factor_ha} de la luz "
             "lunar, así que el fondo que arruina el RGB aquí casi no existe. "
             "Es la noche de las nebulosas de emisión.",
        when=lambda c: _luna(c) >= MOON_MID,
    ),
    Advice(
        key="oiii_luna", technique="oiii", problem="luna", severity="evitar",
        title="OIII no, aunque sea banda estrecha",
        body="El OIII cae en la parte del espectro donde la Luna dispersa más "
             "(Rayleigh va como lambda^-4), así que NO se comporta como el "
             "Halfa: con la Luna al {luna_pct} % el fondo en OIII sube mucho más "
             "que en rojo. Si tienes que elegir un filtro esta noche, es el Halfa.",
        when=lambda c: _luna(c) >= MOON_MID,
    ),
    Advice(
        key="dualband_luna", technique="dual", problem="luna", severity="cuidado",
        title="Con dual-band, la mitad del filtro trabaja a ciegas",
        body="Un dual-band Ha+OIII con la Luna al {luna_pct} % te da un canal "
             "bueno y otro ruidoso: el Halfa aguanta y el OIII no. Si el objeto "
             "vive del OIII (restos de supernova, planetarias), esta noche va a "
             "salir desequilibrado.",
        when=lambda c: _luna(c) >= MOON_MID,
    ),
    Advice(
        key="ifn_luna", technique="luminancia", problem="luna", severity="evitar",
        title="Nada de IFN ni de halos",
        body="El contraste extremo necesita {gate_extremo} mag/arcsec² y esta "
             "noche el cielo se queda en {cielo}. Con la Luna al {luna_pct} % no "
             "hay integración que valga: el IFN, las colas de marea y los halos "
             "de galaxia piden Luna nueva o nada.",
        when=lambda c: _luna(c) >= MOON_LOW,
    ),
    Advice(
        key="luna_nueva", technique="luminancia", problem="luna", severity="aprovecha",
        title="Sin Luna: gasta la noche en lo débil",
        body="Luna al {luna_pct} % y {oscuridad} h de oscuridad astronómica. "
             "Estas noches son las caras: usa el tiempo en luminancia profunda, "
             "IFN, halos y colas de marea, que es lo unico que NO se puede hacer "
             "el resto del mes. El RGB y el Halfa esperan.",
        when=lambda c: _luna(c) < MOON_LOW,
    ),
    Advice(
        key="planetaria_luna", technique="planetaria", problem="luna", severity="aprovecha",
        title="A la Luna y a los planetas les da igual la Luna",
        body="Con la Luna al {luna_pct} % el cielo profundo está comprometido, "
             "pero la planetaria y la lunar no dependen del fondo: son objetos "
             "brillantes y lo que manda es el seeing. Noche perfecta para eso.",
        when=lambda c: _luna(c) >= MOON_MID,
    ),

    # ------------------------------------------------- TRANSPARENCIA/CALIMA --
    Advice(
        key="calima_oiii", technique="oiii", problem="calima", severity="evitar",
        title="Con calima, el OIII es el primero en caer",
        body="AOD de polvo alto (AOD total {aod}, k={k}): el aerosol dispersa más "
             "en azul, así que el OIII y el SII se llevan la peor parte. El Halfa "
             "atraviesa mucho mejor una noche lechosa.",
        when=lambda c: bool(c.dust_alert),
    ),
    Advice(
        key="calima_rgb", technique="rgb", problem="calima", severity="cuidado",
        title="La calima te va a cambiar el color",
        body="Con k={k} el aerosol no solo atenúa, tiñe: el azul pierde más que "
             "el rojo y el balance de color que calibres esta noche no valdra "
             "para otra. Si vas a mezclar con datos de otras noches, saca las "
             "tres tomas seguidas o no mezcles.",
        when=lambda c: bool(c.dust_alert) or (c.k_total is not None and c.k_total > 0.25),
    ),
    Advice(
        key="transparencia_pobre", technique="luminancia", problem="calima",
        severity="cuidado",
        title="Transparencia justa: sube el objeto, no el tiempo",
        body="Con k={k} cada masa de aire cuesta mucho más de lo normal. Elige "
             "objetos altos y descarta lo que este por debajo de 40 grados: "
             "alargar la sesión no compensa una extinción así.",
        when=lambda c: c.k_total is not None and c.k_total > 0.25,
    ),

    # -------------------------------------------------------------- SEEING --
    Advice(
        key="seeing_malo_planetaria", technique="planetaria", problem="seeing",
        severity="evitar",
        title="El seeing no da para planetaria",
        body="Seeing {seeing} (hasta {seeing_alto} arcsec en el borde "
             "pesimista de la banda). La planetaria y la lunar de alta "
             "resolución viven del seeing y esta noche no lo hay. Baja la focal "
             "o cambia de plan.",
        when=lambda c: c.seeing_tercile == "desfavorable",
    ),
    Advice(
        key="seeing_malo_compacto", technique="luminancia", problem="seeing",
        severity="cuidado",
        title="Objetos pequeños, otra noche",
        body="Con seeing {seeing} (hasta {seeing_alto} arcsec) las planetarias "
             "pequeñas, los núcleos y "
             "cualquier cosa que dependa de resolver detalle salen blandos. Lo "
             "extenso y de brillo superficial bajo -- nebulosas grandes, IFN -- "
             "no se entera del seeing: cambia de objetivo, no de noche.",
        when=lambda c: c.seeing_tercile == "desfavorable",
    ),
    Advice(
        key="seeing_bueno", technique="planetaria", problem="seeing",
        severity="aprovecha",
        title="Seeing favorable: gástalo en resolución",
        body="Seeing {seeing}. Es la noche de la planetaria, de las planetarias "
             "pequeñas y de los pares cerrados. Lo extenso y débil da igual "
             "cuando el seeing es bueno: eso lo puedes hacer cualquier noche.",
        when=lambda c: c.seeing_tercile == "favorable",
    ),

    # --------------------------------------------------------------- NUBES --
    Advice(
        key="nubes_probables", technique="cualquiera", problem="nubes",
        severity="cuidado",
        title="Plan corto: la noche no es fiable",
        body="Solo {p_abrir} % de probabilidad de que la noche sea utilizable. "
             "Encadena tomas cortas y guarda cada subframe: una sesión partida "
             "en trozos de 20 minutos se salva; un mosaico de seis paneles, no.",
        when=lambda c: c.open_probability is not None and c.open_probability < 0.6,
    ),
    Advice(
        key="nubes_bueno", technique="cualquiera", problem="nubes",
        severity="aprovecha",
        title="Noche estable: empieza por lo largo",
        body="{p_abrir} % de probabilidad de noche utilizable. Si tienes un "
             "proyecto que necesita muchas horas seguidas -- mosaicos, banda "
             "estrecha profunda -- esta es la noche para adelantarlo.",
        when=lambda c: c.open_probability is not None and c.open_probability >= 0.85,
    ),

    # ------------------------------------------------------ HUMEDAD/ROCIO --
    Advice(
        key="rocio", technique="cualquiera", problem="rocio", severity="cuidado",
        title="Vigila el rocío",
        body="Humedad al {humedad} % y solo {margen_rocio} grados hasta el punto "
             "de rocío. La resistencia de la lente no es opcional esta noche, y "
             "conviene revisar el frontal cada media hora: cuando empaña, las "
             "tomas siguen saliendo y todas valen para nada.",
        when=lambda c: (c.dew_margin is not None and c.dew_margin < 3.0)
                       or (c.humidity is not None and c.humidity > 85),
    ),

    # -------------------------------------------------------------- VIENTO --
    Advice(
        key="viento", technique="cualquiera", problem="viento", severity="cuidado",
        title="El viento te va a costar subframes",
        body="Viento de {viento} km/h. En focal larga el guiado sufre y una "
             "racha arruina la toma entera: baja el tiempo de exposición "
             "individual y da por hecho que vas a descartar más de lo normal.",
        when=lambda c: c.wind is not None and c.wind >= 5.5,
    ),

    # ---------------------------------------------------------- CREPUSCULO --
    Advice(
        key="noche_corta", technique="cualquiera", problem="crepusculo",
        severity="cuidado",
        title="Noche corta: no la repartas",
        body="Solo {oscuridad} h de oscuridad astronómica. Con tan poco tiempo, "
             "repartirlo entre dos objetos deja los dos a medias. Elige uno.",
        when=lambda c: 0 < c.dark_hours < 5.0,
    ),
]


def evaluate(conditions: Conditions) -> list[dict]:
    """Los consejos que aplican a ESTA noche, ordenados por lo que mas manda.

    El orden no es alfabetico ni por tecnica: primero lo que hay que evitar,
    porque es lo que cambia el plan; luego lo que hay que aprovechar, que es lo
    que lo mejora; y al final los cuidados, que son matices.
    """
    orden = {"evitar": 0, "aprovecha": 1, "cuidado": 2}
    disparados = [regla for regla in RULES if regla.when(conditions)]
    disparados.sort(key=lambda r: (orden.get(r.severity, 9), r.technique))
    return [regla.render(conditions) for regla in disparados]
