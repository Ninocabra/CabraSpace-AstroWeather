# -*- coding: utf-8 -*-
"""Que hacer ESTA noche: semaforo por filtro y por familia de objeto.

El producto ya decia como esta la noche y que objetos rinden. Lo que faltaba es
lo que un astrofotografo con experiencia te diria al oido: "con esa Luna no
saques RGB", "hoy Halfa si, OIII no", "el seeing no da para la planetaria".

FORMA: dos filas de semaforos, no una lista de parrafos. Una lista solo ensena
lo que va mal; un semaforo con TODAS las filas ensena tambien lo que va bien, y
de un vistazo. Cada fila trae un veredicto corto y el detalle detras, que la
pagina saca al pasar el cursor.

* **Adquisicion**: RGB, luminancia, Halfa, OIII, SII, dual-band, planetaria.
* **Objetos**: galaxias, nebulosas de emision, de reflexion, planetarias,
  cumulos, e IFN y halos.

Las dos filas se leen cruzadas a proposito: con Luna llena el Halfa esta verde
y el OIII rojo, asi que "nebulosas de emision" sale en ambar -- se pueden, pero
solo por un canal. Eso es una decision de plan, y es la que se venia a tomar.

DE DONDE SALEN LOS UMBRALES. Ninguno se inventa aqui:

* Los cortes de cielo por clase, de `targets.SKY_GATE`; el factor por filtro,
  de `targets.MOON_FILTER_FACTOR` (SPEC 2.3). Si cambian alli, cambia el
  semaforo, y no hay dos verdades.
* Los terciles de seeing, de `seeing.py`.
* **Separacion a la Luna**: el fondo toca su minimo alrededor de 80-90 grados
  del disco (Krisciunas & Schaefer; medido tambien en SAAO y en Weihai), y la
  practica publicada pide **40 grados como minimo para banda ancha** cerca de
  la llena y **50-60 con la Luna llena**. De ahi MOON_SEP_MIN y MOON_SEP_GOOD.
* **Filtros estrechos**: 3 nm suprime la luz lunar bastante mejor que 7-12 nm,
  y Halfa y SII aguantan la Luna mucho mejor que el **OIII**, que cae en la
  parte azul del espectro donde la dispersion Rayleigh (~lambda^-4) es mucho
  mas fuerte. Por eso el OIII tiene fila propia y no hereda el indulto del
  Halfa: es el error tipico de la banda estrecha.
* **Altura**: a 30 grados se atraviesan ~2 masas de aire y queda ~el 81 % del
  flujo cenital; por debajo de 30-35 la dispersion atmosferica empieza a tenir
  los bordes de las estrellas, y la AAVSO recomienda no medir ahi. De ahi
  ALT_MIN_BUENA.
* **Rocio**: se condensa cuando la optica ALCANZA el punto de rocio, y la
  practica es mantener las superficies unos grados por encima. El margen que
  publicamos es aire menos punto de rocio, que es optimista: el frontal radia
  al cielo y esta mas frio que el aire.
"""

from __future__ import annotations

from dataclasses import dataclass

from .targets import MOON_FILTER_FACTOR, SKY_GATE

# Fraccion iluminada de la Luna. Los cortes son los puntos donde el fondo cruza
# las puertas de banda ancha en un sitio de 21,7 mag naturales.
MOON_LOW = 0.25
MOON_MID = 0.55
MOON_HIGH = 0.80

# Separacion al disco, en grados.
MOON_SEP_MIN = 40.0
MOON_SEP_GOOD = 80.0

# Altura por debajo de la cual la masa de aire y la dispersion mandan.
ALT_MIN_BUENA = 30.0

# Margen aire-rocio, en grados.
DEW_CRITICO = 2.0
DEW_VIGILAR = 5.0

# Viento medio, m/s.
VIENTO_VIGILAR = 5.5
VIENTO_MALO = 8.3

# Humedad relativa a partir de la cual el aerosol CRECE con el agua y la
# extincion sube por encima de lo que dice un AOD seco (crecimiento
# higroscopico: el aerosol capta agua, aumenta de tamano y cambia su indice de
# refraccion). La literatura mide un factor 2,2 en la razon de lidar a RH 92 %,
# y la ordenacion espectral de la extincion llega a INVERTIRSE cerca del 92-98 %.
# Aqui se usa como aviso, no como correccion: no medimos el crecimiento.
RH_HIGROSCOPICA = 80.0
RH_CRITICA = 92.0

VERDE, AMBAR, ROJO = "verde", "ambar", "rojo"
LIGHTS = (VERDE, AMBAR, ROJO)


@dataclass
class Conditions:
    """Lo que la noche tiene, ya resumido. Lo que no se sabe queda en None y
    entonces la fila lo dice, en vez de adivinar."""

    moon_illumination: float = 0.0
    moon_altitude_max: float | None = None
    moon_up: bool = True
    sky_mag: float | None = None
    seeing_tercile: str | None = None
    seeing_range: tuple[float, float] | None = None
    aod: float | None = None
    k_total: float | None = None
    dust_alert: bool = False
    open_probability: float | None = None
    dark_hours: float = 0.0
    humidity: float | None = None
    dew_margin: float | None = None
    wind: float | None = None

    @property
    def luna_pct(self) -> int:
        return int(round(self.moon_illumination * 100))

    @property
    def luna_manda(self) -> bool:
        """La Luna molesta de verdad solo si esta ARRIBA y da luz."""
        return self.moon_up and self.moon_illumination >= MOON_LOW

    def cielo(self, decimales: int = 1) -> str:
        return f"{self.sky_mag:.{decimales}f}" if self.sky_mag is not None else "?"

    def aod_txt(self) -> str:
        return f"{self.aod:.3f}" if self.aod is not None else "?"

    def k_txt(self) -> str:
        return f"{self.k_total:.2f}" if self.k_total is not None else "?"


def _peor(*luces: str) -> str:
    for luz in (ROJO, AMBAR, VERDE):
        if luz in luces:
            return luz
    return VERDE


def _fila(clave, etiqueta, luz, resumen, detalle) -> dict:
    return {"clave": clave, "etiqueta": etiqueta, "luz": luz,
            "resumen": resumen, "detalle": " ".join(detalle.split())}


# ============================================================ ADQUISICION ==
def _banda_ancha(c: Conditions, clave: str, etiqueta: str, gate: float) -> dict:
    """RGB y luminancia comparten fisica: el fondo se los come."""
    fondo = c.sky_mag
    if c.luna_manda and fondo is not None and fondo < gate:
        return _fila(clave, etiqueta, ROJO,
                     f"la Luna al {c.luna_pct} % lo tapa",
                     f"""El fondo de cielo se queda en {c.cielo()} mag/arcsec², por
                     debajo de los {gate:.1f} que esta técnica necesita. Apilar no
                     lo arregla: apilar baja el ruido, no el fondo. Y si aun así
                     sales, mantente a más de {MOON_SEP_MIN:.0f}° del disco — por
                     debajo de eso el halo domina el encuadre entero.""")
    if (c.luna_manda and c.moon_illumination >= MOON_MID
            and c.humidity is not None and c.humidity >= RH_HIGROSCOPICA):
        return _fila(clave, etiqueta, ROJO, "Luna y humedad se suman",
                     f"""Con la Luna al {c.luna_pct} % y {c.humidity:.0f} % de
                     humedad, el halo es peor de lo que dice el modelo: el aerosol
                     hidratado dispersa más (Mie), así que la misma Luna ensucia
                     más cielo. Sube la separación mínima al objeto por encima de
                     los {MOON_SEP_MIN:.0f}° habituales y cuenta con un gradiente
                     de fondo fuerte en el encuadre.""")
    if c.luna_manda and c.moon_illumination >= MOON_MID:
        return _fila(clave, etiqueta, AMBAR, "solo objetos brillantes",
                     f"""Con la Luna al {c.luna_pct} % el color sale en cúmulos y
                     galaxias brillantes, pero las partes débiles se quedan dentro
                     del ruido del fondo. El cielo toca su mínimo a unos
                     {MOON_SEP_GOOD:.0f}° del disco: elige objetos lejos de ella y
                     no bajes de {MOON_SEP_MIN:.0f}°.""")
    if c.dust_alert or (c.k_total is not None and c.k_total > 0.25):
        return _fila(clave, etiqueta, AMBAR, "la calima te cambia el color",
                     f"""Con k={c.k_txt()} el aerosol no solo atenúa, tiñe: el azul
                     pierde más que el rojo. El balance de color que calibres esta
                     noche no valdrá para otra, así que saca los canales seguidos o
                     no los mezcles con otras sesiones.""")
    if c.humidity is not None and c.humidity >= RH_HIGROSCOPICA:
        return _fila(clave, etiqueta, AMBAR, "la humedad hincha el aerosol",
                     f"""Cielo despejado no es lo mismo que cielo transparente. Con
                     {c.humidity:.0f} % de humedad el aerosol capta agua y CRECE, y
                     la extinción real sube por encima del k={c.k_txt()} que ves
                     arriba, que sale de un aerosol seco. El efecto se dispara
                     cerca del 92 %. Da por hecho que pierdes algo más de lo que
                     dice la previsión, sobre todo en azul.""")
    return _fila(clave, etiqueta, VERDE, "adelante",
                 f"""Fondo en {c.cielo()} mag/arcsec² contra los {gate:.1f} que pide
                 la técnica, y la Luna no manda ({c.luna_pct} % iluminada{'' if
                 c.moon_up else ', y bajo el horizonte'}). Es la noche de gastar el
                 tiempo en lo que necesita fondo oscuro.""")


def _halfa(c: Conditions) -> dict:
    factor = MOON_FILTER_FACTOR.get("narrowband", 1.0)
    if c.dust_alert:
        return _fila("ha", "Hα", AMBAR, "la calima resta, pero pasa",
                     f"""El Hα es el filtro que mejor atraviesa una noche lechosa:
                     el aerosol dispersa mucho más en azul. Con AOD {c.aod_txt()}
                     pierdes contraste, no la sesión.""")
    if c.moon_illumination >= MOON_MID:
        return _fila("ha", "Hα", VERDE, "la noche es suya",
                     f"""Con la Luna al {c.luna_pct} % el Hα es justo lo que hay que
                     sacar: el filtro deja pasar un factor {factor:.2f} de la luz
                     lunar, así que el fondo que arruina el RGB aquí casi no existe.
                     Con 3 nm la supresión es aún mayor que con 7; si tu filtro es
                     ancho, sube el número de tomas y descarta sin pena.""")
    return _fila("ha", "Hα", VERDE, "bien, pero no lo desperdicies",
                 f"""El Hα funciona, pero con la Luna al {c.luna_pct} % no estás
                 aprovechando su ventaja: banda estrecha se puede hacer cualquier
                 noche con Luna, y lo que NO se puede hacer entonces es banda ancha
                 profunda. Si el cielo está oscuro, gástalo en eso.""")


def _oiii(c: Conditions) -> dict:
    if c.dust_alert:
        return _fila("oiii", "OIII", ROJO, "la calima se lo come",
                     f"""El aerosol dispersa mucho más en azul, así que el OIII es el
                     primero en caer con calima (AOD {c.aod_txt()}, k={c.k_txt()}).
                     Cambia al Hα y deja el OIII para una noche limpia.""")
    if c.luna_manda and c.moon_illumination >= MOON_MID:
        return _fila("oiii", "OIII", ROJO, "no hereda el indulto del Hα",
                     f"""Este es el error típico de la banda estrecha. El OIII cae en
                     la parte azul del espectro, donde la dispersión Rayleigh va como
                     λ⁻⁴, así que la Luna le afecta mucho más que al Hα: con
                     {c.luna_pct} % iluminada el fondo en OIII sube de largo. Si solo
                     puedes elegir un filtro esta noche, es el Hα.""")
    if c.humidity is not None and c.humidity >= RH_CRITICA:
        return _fila("oiii", "OIII", AMBAR, "la humedad le pega en su banda",
                     f"""Con {c.humidity:.0f} % de humedad el aerosol hidratado
                     dispersa mucho más, y lo hace sobre todo en azul, que es
                     justo donde vive el OIII. Cerca del 92 % la extinción llega a
                     invertir su orden espectral. No es una noche perdida, pero el
                     contraste que saques hoy no lo vas a poder mezclar con el de
                     una noche seca.""")
    if c.luna_manda:
        return _fila("oiii", "OIII", AMBAR, "vigila la separación",
                     f"""Con la Luna al {c.luna_pct} % el OIII aún trabaja, pero es el
                     filtro más sensible que tienes: mantén el objeto por encima de
                     {MOON_SEP_GOOD:.0f}° del disco, que es donde el fondo toca su
                     mínimo.""")
    return _fila("oiii", "OIII", VERDE, "condiciones suyas",
                 f"""Sin Luna que dispersar en azul y con k={c.k_txt()}, el OIII da lo
                 que puede dar. Es la señal más difícil de la banda estrecha:
                 aprovéchalo en restos de supernova y planetarias.""")


def _sii(c: Conditions) -> dict:
    if c.dust_alert:
        return _fila("sii", "SII", AMBAR, "aguanta la calima",
                     """El SII está aún más al rojo que el Hα, así que la calima le
                     afecta menos que a nadie. Lo que le sobra de robustez le falta
                     de señal: es la línea más débil de las tres y necesita bastante
                     más integración.""")
    if c.moon_illumination >= MOON_MID:
        return _fila("sii", "SII", VERDE, "aguanta como el Hα",
                     f"""El SII es tan robusto a la Luna como el Hα y por lo mismo:
                     está en el rojo. Con {c.luna_pct} % iluminada se saca sin
                     problema. Cuenta con integrar más que en Hα, porque la línea es
                     intrínsecamente más débil.""")
    return _fila("sii", "SII", VERDE, "adelante",
                 """Sin Luna, el SII no gana nada especial respecto a otras noches: si
                 la paleta que buscas lo necesita, adelante, pero el cielo oscuro
                 rinde más en banda ancha profunda.""")


def _dual(c: Conditions) -> dict:
    if c.luna_manda and c.moon_illumination >= MOON_MID:
        return _fila("dual", "Dual-band", AMBAR, "medio filtro a ciegas",
                     f"""Un dual Hα+OIII con la Luna al {c.luna_pct} % te da un canal
                     bueno y otro ruidoso: el Hα aguanta y el OIII no. Si el objeto
                     vive del OIII — restos de supernova, planetarias grandes — la
                     toma va a salir desequilibrada y lo verás al combinar. En
                     emisión pura de Hα no lo notarás.""")
    if c.dust_alert:
        return _fila("dual", "Dual-band", AMBAR, "el canal azul sufre",
                     """Con calima el canal OIII pierde y el Hα no: el mismo
                     desequilibrio que con Luna, por otra causa.""")
    return _fila("dual", "Dual-band", VERDE, "los dos canales trabajan",
                 """Sin Luna alta ni calima, los dos canales del dual rinden a la vez,
                 que es la única situación en la que el filtro hace lo que
                 promete.""")


def _infrarrojo(c: Conditions) -> dict:
    """El IR cercano: lo que atraviesa lo que los demas no.

    La dispersion Rayleigh va como lambda^-4 y la de Mie del aerosol cae
    tambien con la longitud de onda, asi que a 700-1000 nm tanto la luz lunar
    como la calima molestan MUCHO menos que en visible -- es el mismo argumento
    que hace al Halfa robusto, llevado mas lejos. Ademas el seeing mejora con
    lambda^(1/5): una noche mediocre en visible es algo mejor en IR.

    Lo que cuesta: casi ninguna camara color sirve (el filtro de bloqueo IR va
    de serie y hay que quitarlo), el foco se desplaza respecto al visible en
    casi cualquier optica que no sea apocromatica de verdad, y el objeto tiene
    que EMITIR ahi -- polvo, gigantes rojas, nebulosas de reflexion -- porque
    en IR no hay lineas de emision que valgan.
    """
    if c.dust_alert:
        return _fila("ir", "Infrarrojo", VERDE, "es lo que mejor atraviesa",
                     f"""Con calima (AOD {c.aod_txt()}, k={c.k_txt()}) el IR es la
                     banda que menos pierde: la dispersion cae con la longitud de
                     onda, asi que donde el OIII se ahoga el IR pasa. Si tienes
                     camara sin filtro de bloqueo, esta es su noche. Cuenta con
                     reenfocar: el punto focal del IR no es el del visible.""")
    if c.humidity is not None and c.humidity >= RH_CRITICA:
        return _fila("ir", "Infrarrojo", AMBAR, "la banda J es la que sufre",
                     f"""El IR aguanta la humedad mejor que el visible, pero no
                     todo por igual: con vapor de agua alto la banda J pierde
                     bastante -- en la literatura, hasta un 21 % de transmisión
                     entre 1 y 10 mm de agua precipitable -- mientras H y K se
                     mueven menos del 5 %. Con {c.humidity:.0f} % de humedad
                     relativa en superficie, prioriza H o K si tu filtro llega, y
                     no compares fotometría de J entre noches. AVISO: el agua
                     precipitable NO se prevé aquí todavía; la humedad de
                     superficie es un indicio, no la medida.""")
    if c.luna_manda and c.moon_illumination >= MOON_MID:
        return _fila("ir", "Infrarrojo", VERDE, "la Luna le molesta poco",
                     f"""Mismo argumento que hace robusto al Halfa, llevado mas
                     lejos: a {c.luna_pct} % de Luna, el fondo en IR sube mucho
                     menos que en visible porque el Rayleigh va como lambda^-4. Y
                     el seeing mejora con lambda^(1/5), asi que ademas resuelve algo
                     mejor. Pide objeto que EMITA ahi -- polvo, gigantes rojas,
                     reflexion -- y camara sin filtro de bloqueo IR.""")
    if c.seeing_tercile == "desfavorable":
        alto = f'{c.seeing_range[1]:.1f}"' if c.seeing_range else "?"
        return _fila("ir", "Infrarrojo", AMBAR, "salva algo del seeing",
                     f"""Con seeing {c.seeing_tercile} (hasta {alto}) el IR resuelve
                     algo mejor, porque el seeing escala como lambda^(1/5). Es una
                     mejora real pero pequena: no convierte una noche mala en buena,
                     la deja algo menos mala.""")
    return _fila("ir", "Infrarrojo", AMBAR, "sin ventaja esta noche",
                 f"""El IR gana cuando hay algo que atravesar -- Luna, calima, mal
                 seeing -- y esta noche no lo hay: cielo en {c.cielo()} y seeing
                 {c.seeing_tercile or 'sin dato'}. Con el cielo asi, la banda ancha
                 normal recoge mucha mas senal por hora.""")


def _planetaria(c: Conditions) -> dict:
    alto = f"{c.seeing_range[1]:.1f}" if c.seeing_range else "?"
    if c.seeing_tercile == "desfavorable":
        return _fila("planetaria", "Planetaria y lunar", ROJO, "el seeing no da",
                     f"""Seeing {c.seeing_tercile} (hasta {alto}" en el borde
                     pesimista de la banda). La alta resolución vive del seeing y
                     esta noche no lo hay: baja la focal o cambia de plan. El fondo
                     de cielo, en cambio, le da igual — la Luna no es el problema
                     aquí. De dónde sale este número: del viento en el chorro, a
                     200 hPa, que es el predictor de altura que usa la literatura.
                     Lo que NO ve es la capa de suelo, que es la que más manda y
                     que aquí nadie mide — por eso esto va en terciles y sin
                     calibrar.""")
    if c.wind is not None and c.wind >= VIENTO_MALO:
        return _fila("planetaria", "Planetaria y lunar", AMBAR,
                     "el viento vibra el tubo",
                     f"""Con {c.wind * 3.6:.0f} km/h la vibración se ve a focal larga
                     aunque el seeing acompañe. Vídeos cortos y mucho descarte.""")
    if c.seeing_tercile == "favorable":
        return _fila("planetaria", "Planetaria y lunar", VERDE,
                     "gástalo en resolución",
                     f"""Seeing {c.seeing_tercile} (banda hasta {alto}"): es la noche
                     de la planetaria, de las planetarias pequeñas y de los pares
                     cerrados. Que la Luna esté al {c.luna_pct} % no le afecta — son
                     objetos brillantes y aquí manda la turbulencia, no el fondo.""")
    return _fila("planetaria", "Planetaria y lunar", AMBAR, "seeing del montón",
                 f"""Seeing {c.seeing_tercile or 'sin dato'} (banda hasta {alto}"): se
                 puede intentar, pero no es la noche de batir tu mejor imagen. La
                 Luna al {c.luna_pct} % no estorba a esta técnica.""")


# ================================================================ OBJETOS ==
def _familia(c: Conditions, clave, etiqueta, gate_clave, *, extra="") -> dict:
    """Una familia de objetos contra la puerta de cielo de su clase."""
    gate = SKY_GATE[gate_clave]
    fondo = c.sky_mag
    if fondo is not None and c.luna_manda and fondo < gate:
        return _fila(clave, etiqueta, ROJO, f"pide {gate:.1f} y hay {c.cielo()}",
                     f"""El cielo se queda en {c.cielo()} mag/arcsec² y esta familia
                     necesita {gate:.1f} para que la señal salga del fondo. Con la
                     Luna al {c.luna_pct} % no hay integración que lo compense.
                     {extra}""")
    if fondo is not None and fondo < gate + 0.6:
        return _fila(clave, etiqueta, AMBAR, "justo en el límite",
                     f"""El cielo está en {c.cielo()} contra los {gate:.1f} de la
                     clase: sale, pero sin margen. Sube el objeto por encima de
                     {ALT_MIN_BUENA:.0f}° — a esa altura ya cruzas dos masas de aire
                     y pierdes cerca de un 20 % del flujo. {extra}""")
    return _fila(clave, etiqueta, VERDE, "condiciones de sobra",
                 f"""Cielo en {c.cielo()} contra los {gate:.1f} que pide la clase.
                 {extra or f'Elige por altura: por encima de {ALT_MIN_BUENA:.0f}° la '
                            'masa de aire deja de ser el problema.'}""")


def _emision(c: Conditions) -> dict:
    """La unica familia que cruza las dos filas: depende del FILTRO."""
    ha, oiii = _halfa(c), _oiii(c)
    if ha["luz"] == VERDE and oiii["luz"] == ROJO:
        return _fila("emision", "Nebulosas de emisión", AMBAR,
                     "sí, pero solo en Hα",
                     f"""Es la familia que salva la noche con Luna: con {c.luna_pct} %
                     iluminada y el cielo en {c.cielo()}, en Hα está verde y en OIII
                     rojo. Si tu objeto es emisión pura — Norteamérica, Corazón,
                     Roseta — adelante. Si vive del OIII, como el Velo o una
                     planetaria grande, déjalo para otra noche.""")
    return _fila("emision", "Nebulosas de emisión", _peor(ha["luz"], oiii["luz"]),
                 "según el filtro",
                 f"""Hα: {ha['resumen']}. OIII: {oiii['resumen']}. La familia entera
                 depende de por qué canal vive tu objeto.""")


def _ifn(c: Conditions) -> dict:
    fila = _familia(c, "ifn", "IFN, halos y colas de marea", "extremo_contraste",
                    extra="Es el objetivo más exigente en contraste que existe: "
                          "o Luna nueva, o nada.")
    if fila["luz"] == VERDE and c.dark_hours and c.dark_hours < 5:
        return _fila("ifn", "IFN, halos y colas de marea", AMBAR,
                     "cielo sí, tiempo no",
                     f"""El fondo da, pero solo hay {c.dark_hours:.1f} h de oscuridad
                     astronómica. Este tipo de señal se construye con muchas horas:
                     repartir una noche corta entre dos objetos deja los dos a
                     medias.""")
    return fila


def _cumulos(c: Conditions) -> dict:
    """Los cumulos no se miden con la puerta de brillo superficial.

    `SKY_GATE` describe cuanto fondo aguanta una SUPERFICIE extendida, y un
    cumulo son estrellas: fuentes puntuales cuya senal no se diluye con el
    fondo del mismo modo. Pasarlos por la puerta "normal" los pintaba en rojo
    con Luna llena, y eso es falso -- un globular brillante se saca con Luna
    llena y sale bien. Lo que se pierde son las estrellas debiles del halo y
    cualquier nebulosidad de fondo, no el cumulo.
    """
    if c.luna_manda and c.moon_illumination >= MOON_MID:
        return _fila("cumulos", "Cúmulos", AMBAR, "el cúmulo sí, el fondo no",
                     f"""Son estrellas, no superficie: con la Luna al {c.luna_pct} %
                     un globular brillante sale igual, y por eso son el mejor plan B
                     de una noche mala. Lo que pierdes son las estrellas débiles del
                     halo y cualquier nebulosidad alrededor. Ojo al enfoque, que aquí
                     lo notas todo, y a no saturar el núcleo.""")
    return _fila("cumulos", "Cúmulos", VERDE, "siempre disponibles",
                 f"""Es lo último que pierde una noche mediocre. Con el cielo en
                 {c.cielo()} salen hasta las estrellas débiles del halo. Lo que los
                 decide de verdad es el seeing, que esta noche es
                 {c.seeing_tercile or 'desconocido'}.""")


def _planetarias_obj(c: Conditions) -> dict:
    if c.seeing_tercile == "desfavorable":
        alto = f'{c.seeing_range[1]:.1f}"' if c.seeing_range else "sin banda"
        return _fila("planetarias", "Nebulosas planetarias", AMBAR,
                     "las pequeñas, no",
                     f"""Son compactas y de alto brillo superficial: el fondo les
                     molesta poco y el seeing mucho. Con la turbulencia de esta noche
                     (hasta {alto}), las grandes sí y las pequeñas salen blandas.""")
    alto = f'{c.seeing_range[1]:.1f}"' if c.seeing_range else "sin banda"
    return _fila("planetarias", "Nebulosas planetarias", VERDE,
                 "toleran el fondo",
                 f"""Alto brillo superficial: con el cielo en {c.cielo()} aguantan la
                 Luna mejor que ninguna otra familia. Lo que las decide es el seeing,
                 y esta noche es {c.seeing_tercile or 'desconocido'} (hasta {alto}).
                 Ojo si tu objeto vive del OIII, que es donde muchas tienen su
                 señal.""")


# ============================================================== EVALUACION ==
def evaluate(conditions: Conditions) -> dict:
    """Las dos filas de semaforos. Siempre TODAS, verdes incluidas.

    Devolver solo lo que va mal seria mas corto y menos util: quien mira esto
    esta decidiendo un plan, y para decidir necesita ver tambien lo que SI
    puede hacer.
    """
    c = conditions
    adquisicion = [
        _banda_ancha(c, "rgb", "RGB / color", SKY_GATE["normal"]),
        _banda_ancha(c, "luminancia", "Luminancia", SKY_GATE["bajo_contraste"]),
        _halfa(c), _oiii(c), _sii(c), _dual(c), _infrarrojo(c),
        _planetaria(c),
    ]
    objetos = [
        _familia(c, "galaxias", "Galaxias", "normal"),
        _emision(c),
        _familia(c, "reflexion", "Nebulosas de reflexión", "bajo_contraste",
                 extra="Son luz de estrellas reflejada en polvo: banda ancha "
                       "obligatoria, y por eso la Luna les hace todo el daño. "
                       "Nada de banda estrecha aquí."),
        _planetarias_obj(c),
        _cumulos(c),
        _ifn(c),
    ]
    return {"adquisicion": adquisicion, "objetos": objetos,
            "avisos": _avisos(c), "limitante": limitante(c),
            "resumen": _titular(c, adquisicion, objetos),
            "referencias": REFERENCIAS}


# De donde sale cada correlacion que se usa arriba. Va DENTRO del JSON por la
# misma razon que la procedencia de cada numero: un consejo que no se puede
# rastrear no se puede discutir, y aqui se esta diciendo a alguien que cambie
# lo que iba a hacer esta noche.
REFERENCIAS = [
    {"tema": "Separación a la Luna",
     "dice": "El fondo toca su mínimo alrededor de 80-90° del disco; la práctica "
             "publicada pide 40° como mínimo en banda ancha cerca de la llena.",
     "usado_en": ["rgb", "luminancia", "oiii"]},
    {"tema": "Filtros estrechos y luz lunar",
     "dice": "3 nm suprime la Luna bastante mejor que 7-12 nm. Hα y SII aguantan; "
             "el OIII no, porque cae en el azul donde Rayleigh va como λ⁻⁴.",
     "usado_en": ["ha", "oiii", "sii", "dual"]},
    {"tema": "Humedad y aerosol (crecimiento higroscópico)",
     "dice": "Con humedad alta el aerosol capta agua y crece: sube la extinción "
             "por encima de la que predice un AOD seco, y cerca del 92 % la "
             "ordenación espectral llega a invertirse. También hace que la misma "
             "Luna disperse más (Mie).",
     "usado_en": ["rgb", "luminancia", "oiii", "ir"]},
    {"tema": "Vapor de agua e infrarrojo",
     "dice": "Entre 1 y 10 mm de agua precipitable, la banda J pierde hasta un "
             "21 % de transmisión mientras H y K se mueven menos del 5 %.",
     "usado_en": ["ir"]},
    {"tema": "Seeing y viento en altura",
     "dice": "El viento a 200 hPa (el chorro, ~12 km) es el predictor de altura "
             "habitual y correla con el empeoramiento del seeing; pero la capa de "
             "suelo domina y no se mide aquí.",
     "usado_en": ["planetaria", "planetarias", "ir"]},
    {"tema": "Altura y masa de aire",
     "dice": "A 30° se cruzan ~2 masas de aire y queda cerca del 81 % del flujo "
             "cenital; por debajo de 30-35° la dispersión atmosférica empieza a "
             "teñir los bordes de las estrellas.",
     "usado_en": ["galaxias", "emision", "reflexion", "cumulos", "ifn"]},
    {"tema": "Rocío",
     "dice": "Condensa cuando la óptica ALCANZA el punto de rocío; la práctica es "
             "mantener las superficies unos grados por encima. El margen que "
             "publicamos (aire − rocío) es optimista: el frontal radia al cielo.",
     "usado_en": ["rocio"]},
]


def _avisos(c: Conditions) -> list[dict]:
    """Lo que no es tecnica ni objeto: el equipo y la noche."""
    salida = []
    if c.dew_margin is not None and c.dew_margin < DEW_VIGILAR:
        luz = ROJO if c.dew_margin < DEW_CRITICO else AMBAR
        salida.append(_fila(
            "rocio", "Rocío", luz, f"{c.dew_margin:.1f}° de margen",
            f"""El aire está a {c.dew_margin:.1f}° del punto de rocío, y este margen
            es OPTIMISTA: el frontal radia al cielo y está más frío que el aire, así
            que condensa antes. Resistencia puesta y parasol largo; cuando empaña,
            las tomas siguen saliendo y todas valen para nada."""))
    if c.wind is not None and c.wind >= VIENTO_VIGILAR:
        luz = ROJO if c.wind >= VIENTO_MALO else AMBAR
        salida.append(_fila(
            "viento", "Viento", luz, f"{c.wind * 3.6:.0f} km/h",
            f"""A focal larga el guiado sufre y una racha arruina la toma entera:
            baja el tiempo de exposición individual y cuenta con descartar más de lo
            normal. Aviso: la racha prevista no está calibrada contra ninguna verdad
            terreno y el modelo se ha medido corto en viento medio."""))
    if c.open_probability is not None and c.open_probability < 0.6:
        salida.append(_fila(
            "nubes", "Nubes", AMBAR, f"{c.open_probability*100:.0f} % utilizable",
            """Encadena tomas cortas y guarda cada subframe: una sesión partida en
            trozos de veinte minutos se salva; un mosaico de seis paneles, no."""))
    if c.dark_hours and c.dark_hours < 5:
        salida.append(_fila(
            "noche_corta", "Noche corta", AMBAR, f"{c.dark_hours:.1f} h de oscuridad",
            """Con tan poco tiempo, repartirlo entre dos objetos deja los dos a
            medias. Elige uno."""))
    return salida


def limitante(c: Conditions) -> str | None:
    """Cual de las casillas del resumen es la que hoy manda, o None.

    Devuelve la clave de la casilla -- `luna`, `cielo`, `seeing`,
    `transparencia` u `oscuridad` -- para que la pagina pueda pintarla en rojo.
    El orden es el de cuanto estropea la noche, no el de la pantalla.
    """
    if c.luna_manda and c.moon_illumination >= MOON_MID:
        return "luna"
    if c.dust_alert:
        return "transparencia"
    if c.seeing_tercile == "desfavorable":
        return "seeing"
    if c.k_total is not None and c.k_total > 0.25:
        return "transparencia"
    if c.dark_hours and c.dark_hours < 5:
        return "oscuridad"
    if c.luna_manda:
        return "luna"
    return None


def _lista(nombres: list[str]) -> str:
    if len(nombres) == 1:
        return nombres[0]
    return ", ".join(nombres[:-1]) + " y " + nombres[-1]


def _titular(c: Conditions, adquisicion: list, objetos: list) -> str:
    """Todo lo que esta en verde, dicho entero.

    Antes nombraba un verde y un rojo de ejemplo, y eso obliga a leerse las
    dos filas para saber si te has perdido algo. Si el resumen no es
    exhaustivo, no es un resumen: es una muestra.
    """
    if c.open_probability is not None and c.open_probability < 0.5:
        return (f"Noche poco fiable ({c.open_probability*100:.0f} % de que sea "
                f"utilizable): plan corto y tomas cortas.")
    tecnicas = [f["etiqueta"] for f in adquisicion if f["luz"] == VERDE]
    familias = [f["etiqueta"] for f in objetos if f["luz"] == VERDE]
    if not tecnicas and not familias:
        return "Ninguna tecnica ni familia esta comoda esta noche."
    partes = []
    if tecnicas:
        partes.append(f"Adelante con {_lista(tecnicas)}")
    if familias:
        partes.append(("apunta a " if tecnicas else "Apunta a ")
                      + _lista([f.lower() for f in familias]))
    # Punto y coma entre las dos mitades: con "y" la frase encadena dos "y"
    # seguidas -- "...y Planetaria y lunar y apunta a..." -- y se lee fatal.
    frase = "; ".join(partes) + "."
    rojos = [f["etiqueta"] for f in adquisicion + objetos if f["luz"] == ROJO]
    if rojos:
        frase += f" Hoy no: {_lista(rojos)}."
    return frase
