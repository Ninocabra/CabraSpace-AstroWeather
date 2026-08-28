"""La ley de dispersion lunar MEDIDA en este sitio (SPEC v8 seccion 2.2).

Krisciunas & Schaefer 1991 se ajusto en Mauna Kea, a 4200 m y por encima de la
capa de inversion. Nerpio esta a 1650 m, con 1,38 veces la columna de aire y con
polvo sahariano encima varias veces al ano. Sobre 131.096 medidas de 10 minutos
de noche cerrada (2.601 noches, 2019-2026) el modelo publicado se queda corto, y
no por un factor: el error crece de forma continua segun sube la Luna, desde
1,2x con la Luna cerca del horizonte hasta 5,5x con la Luna alta.

QUE ES ESTO Y QUE NO ES
-----------------------
Durante un dia se creyo que el exceso era luz parasita del propio fotometro. No
lo es, y lo que lo decide es la Luna BAJO el horizonte: la luz parasita necesita
el haz directo sobre el instrumento, asi que cuando la Tierra lo corta tiene que
apagarse de golpe. No se apaga. Con el centro de la Luna por debajo de -0,83
grados (refraccion y semidiametro incluidos, es decir con la Luna ya invisible)
el exceso sigue valiendo +0,19 mag a 5-7 sigma, y decae con la escala de la
sombra geometrica de la atmosfera:

    depresion   sombra    columna iluminada    exceso medido
      0,25 deg    0,06 km       99,2 %            102,9 %
      0,75        0,55          93,4               87,2
      1,75        2,97          69,0               66,1
      2,50        6,07          46,8               24,6
      3,50       11,91          22,6               11,0

Control con Luna nueva en la misma franja de altura: +0,009 mag. La medida
decae algo mas rapido que la geometria pura, que es lo que se espera al anadir
la extincion rasante del haz que la cuenta geometrica ignora.

Los otros tres candidatos, descartados por medida:

* NUBES iluminadas por la Luna, que tienen el mismo signo. Exigiendo horas
  planas (dispersion intrahoraria < 0,02 mag, donde sin Luna el modelo clava el
  cero: residuo +0,004) el exceso sigue entero: -0,67 mag a 20-30 grados de
  altura lunar y -1,02 a 30-40.
* La FASE. A altura fija el cociente medido/modelo no se mueve con la fase.
* El AEROSOL. Sobre 1.730 horas estables con AOD de CAMS, un rango de 54x
  (0,010 a 0,540), la funcion de dispersion medida a theta fijo se mueve entre
  x0,97 y x1,15. Plana. Por eso el termino que falta NO puede parametrizarse
  con el AOD: lo que sobra es la parte estable de la atmosfera, no el polvo del
  dia.

LA FORMA
--------
Se mide la funcion de dispersion despejandola de la propia estructura de K&S,

    f(theta) = (brillo medido - suelo natural) / (iluminancia * transmision del
               haz * termino del blanco)

y sobre 18.834 muestras de 1.246 noches de AJUSTE (las reservadas no entran)
sale una exponencial unica:

    f(theta) = 1,3612e7 * 10^(-theta / 48,55 deg)      rms 3,8 %

Fijar ademas un termino Rayleigh en el valor que exige la columna de aire del
sitio cuesta 17,5 % de rms y deja un patron sistematico en arco: la curva medida
NO se aplana a angulos grandes, que es justo lo que un termino Rayleigh impone.
Asi que no se fuerza, y con ello se renuncia a separar Rayleigh de Mie: entre 12
y 80 grados los dos terminos describen lo mismo y el ajuste libre manda el
Rayleigh a cero, que es una degeneracion y no un resultado. Esta funcion es una
descripcion empirica de la suma, no una descomposicion fisica.

LA INCERTIDUMBRE QUE HAY QUE CITAR
----------------------------------
NO es el 3,8 % del ajuste: eso mide como de bien pasa la curva por unas medianas
de bin. La cota honesta es la reproducibilidad **entre anios a geometria fija**,
y vale **+-17 % (0,17 mag)**. Esta dominada por algo que no es la dispersion:
la mediana del cielo SIN Luna sube 0,56 mag entre 2019 y 2026 (21,32 -> 21,88),
de forma monotona, y con el ciclo solar prediciendo el signo contrario. Como el
termino lunar se despeja restando el suelo, un suelo que deriva se convierte en
un termino lunar que deriva. Problema abierto, SPEC v8 seccion 2.2 bis.

Queda tambien un +11 % local en la franja de 30-40 grados de altura lunar que la
exponencial no captura. NO se ha metido en la ley, y a proposito: dura solo unos
8 grados (una funcion de fase no hace eso), oscila entre 0,97 y 1,18 segun el
anio, y depende de la hora de la noche. Ajustarlo seria fijar el ruido de la
deriva de arriba en una ley de fisica.

EL LIMITE HONESTO
-----------------
Con un fotometro cenital, el angulo de dispersion y la altura de la Luna son la
misma variable: theta = 90 - altura. No se pueden separar aqui la forma angular
f(theta) y la transmision del haz C(altura), y por debajo de 12 grados de altura
solo esta identificado su PRODUCTO. El reparto que hace este modulo es una
convencion util, no una medida. Y aplicar f(theta) a un blanco que no sea el
cenit hereda la estructura de K&S sin haberla comprobado: eso sigue siendo un
supuesto.
"""

from __future__ import annotations

import math

# Amplitud y escala de la funcion de dispersion, ajustadas aqui (ver arriba).
SCATTER_AMPLITUDE = 1.3612e7
SCATTER_SCALE_DEG = 48.55
# El ajuste solo cubre 12-80 grados; fuera de ese rango el valor se extrapola.
FIT_MIN_SEPARATION_DEG = 12.0
FIT_MAX_SEPARATION_DEG = 80.0
# Por debajo de esta separacion K&S cambia a su expresion de aureola. Se
# conserva su forma rho^-2, reescalada para EMPALMAR con la ley del sitio en vez
# de saltar: el original salta un factor 2 justo en la juntura.
SMALL_SEPARATION_DEG = 10.0

# La Luna dentro del campo del fotometro (TESS-W, ~17 grados a media altura).
# Ahi el instrumento mira la Luna y la medida deja de ser brillo de cielo: son
# 144 muestras de 131.096, y se excluyen del ajuste y de la verdad de nubes.
IN_FIELD_DEG = 12.0

# Transmision del haz lunar hacia el volumen dispersor, medida en franjas de
# altura sobre 23.803 muestras de noches de ajuste. Es 1 con la Luna por encima
# de 8 grados, cae al acercarse al horizonte -- donde el airmass acotado de K&S
# extingue de menos -- y sigue cayendo por debajo, con la Luna ya invisible,
# siguiendo la sombra geometrica de la atmosfera.
#
# Medida contra la `scattering` de este modulo, suelo incluido: el suelo manda
# por encima de 83 grados de separacion, o sea por debajo de 7 grados de altura
# lunar, que es justo el tramo que esta tabla describe.
#
# Y medida SOLO donde la Luna pone al menos el 10 % de la luz, que es la misma
# condicion con la que se valida. Sin ella, a 3 grados de altura con un cuarto
# creciente el modelo lunar es un 5 % del cielo, asi que el sesgo del suelo --
# que se ajusto aparte y vale un 2 % -- se convierte en un 40 % de error
# aparente aqui. La tabla anterior, ajustada solo con `fase < 90`, salia hasta
# un 12 % baja por eso.
HORIZON_ALTITUDE_DEG = (-6.0, -5.0, -4.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0,
                        3.0, 4.0, 5.0, 6.2, 8.0)
HORIZON_TRANSMISSION = (0.000, 0.015, 0.024, 0.052, 0.090, 0.153, 0.243, 0.345,
                        0.432, 0.542, 0.663, 0.784, 0.877, 1.000)
# Por debajo, la columna de aire sobre el sitio esta en sombra: 35 km a -6 grados.
HORIZON_FLOOR_DEG = HORIZON_ALTITUDE_DEG[0]
FULL_TRANSMISSION_DEG = HORIZON_ALTITUDE_DEG[-1]


def _mauna_kea(separation_deg: float) -> float:
    """La funcion publicada, copiada aqui para servir de SUELO.

    Es la misma expresion que `moonsky.scattering`; un test comprueba que no se
    separan. Vive aqui duplicada porque `moonsky` importa este modulo y no puede
    haber ciclo.
    """
    rho = math.radians(separation_deg)
    return ((10 ** 5.36) * (1.06 + math.cos(rho) ** 2)
            + 10 ** (6.15 - separation_deg / 40.0))


def scattering(separation_deg: float) -> float:
    """Funcion de dispersion de este sitio, para un angulo Luna-blanco.

    Sustituye a `moonsky.scattering`, que es la de Mauna Kea y se conserva ahi
    como referencia publicada y como control de los tests.

    Con un SUELO en la funcion publicada, que es lo unico que impide extrapolar
    una exponencial pura mas alla de los 80 grados que se midieron: a 150 grados
    la exponencial sola daria 37 veces MENOS que Mauna Kea, y este sitio tiene
    mas aire y mas polvo encima que Mauna Kea, asi que no puede dispersar menos.
    El suelo solo manda por encima de unos 83 grados, es decir justo fuera del
    rango ajustado.
    """
    if separation_deg < SMALL_SEPARATION_DEG:
        # Aureola: la forma rho^-2 del articulo, con la constante puesta para
        # empalmar en SMALL_SEPARATION_DEG. Un fotometro cenital no puede medir
        # esto, asi que es la unica parte importada tal cual.
        joint = SCATTER_AMPLITUDE * 10 ** (-SMALL_SEPARATION_DEG / SCATTER_SCALE_DEG)
        return joint * (SMALL_SEPARATION_DEG / max(separation_deg, 0.25)) ** 2
    return max(SCATTER_AMPLITUDE * 10 ** (-separation_deg / SCATTER_SCALE_DEG),
               _mauna_kea(separation_deg))


def beam_transmission(moon_altitude_deg: float) -> float:
    """Fraccion del haz lunar que llega al aire que el blanco tiene encima.

    Vale 1 con la Luna alta y baja de forma continua al cruzar el horizonte: no
    hay escalon, que es a la vez el resultado fisico y la razon por la que este
    termino existe.
    """
    if moon_altitude_deg <= HORIZON_FLOOR_DEG:
        return 0.0
    if moon_altitude_deg >= FULL_TRANSMISSION_DEG:
        return 1.0
    for index in range(1, len(HORIZON_ALTITUDE_DEG)):
        high = HORIZON_ALTITUDE_DEG[index]
        if moon_altitude_deg <= high:
            low = HORIZON_ALTITUDE_DEG[index - 1]
            weight = (moon_altitude_deg - low) / (high - low)
            return (HORIZON_TRANSMISSION[index - 1] * (1.0 - weight)
                    + HORIZON_TRANSMISSION[index] * weight)
    return 1.0


def is_extrapolated(separation_deg: float) -> bool:
    """Si el angulo cae fuera del rango en el que se midio la ley."""
    return not (FIT_MIN_SEPARATION_DEG <= separation_deg <= FIT_MAX_SEPARATION_DEG)


def shadow_height_km(moon_depression_deg: float) -> float:
    """Altura hasta la que la Tierra tapa la Luna, para una depresion dada.

    No entra en el modelo: es la cuenta con la que se comprobo que la caida de
    `beam_transmission` bajo el horizonte es geometrica y no instrumental.
    """
    if moon_depression_deg <= 0:
        return 0.0
    return 6371.0 * (1.0 / math.cos(math.radians(moon_depression_deg)) - 1.0)
