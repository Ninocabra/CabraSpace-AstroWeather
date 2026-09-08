"""Calibrar la altura de escala del aerosol, `AEROSOL_SCALE_HEIGHT_M` (SPEC v8 2.1).

Ese numero -- 1700 m -- lleva desde el principio con el comentario "calibrable
contra AERONET" y nunca se ha calibrado. Manda mas de lo que parece: con la
rama sin especies, `site_aod = aod_total * exp(-583/H_a)`, y H_a entre 500 m y
20 km cambia el AOD del sitio por un factor **tres**, o sea la k publicada y el
umbral de calima con ella.

Este modulo intenta medirlo, y sobre todo SABE DECIR QUE NO PUEDE. Medido el
08-09-2026 contra el archivo entero, la respuesta hoy es que no.

## Por que no sale, y no es falta de paciencia

El modelo es lineal en el observable:

    Y = a + B * [ aod_dust + f * (aod_total - aod_dust) ],   f = exp(-dh/H_a)

**Sin especies (`aod_dust` ausente), H_a es INDETERMINABLE por construccion.**
Queda `Y = a + (B*f) * aod_total`: solo el producto se puede estimar, y
cualquier H_a encaja igual de bien con una B que lo compense. Comprobado sobre
3.200 horas limpias: el R2 sale **identico a doce decimales** para H_a de 500 m
a 20 km. Un ajuste que "converge" ahi no ha medido nada.

**Con especies tampoco, con el fotometro como observable.** Tres numeros
medidos:

* Señal/ruido por hora: **0,138**. El cielo se mueve 0,033 mag con el AOD
  tipico y su propia dispersion es 0,241 mag.
* Colinealidad `aod_dust` ~ `aod_total`: **r = 0,957**. Los dos regresores son
  casi la misma variable, asi que separar sus coeficientes esta mal
  condicionado.
* Horas limpias CON especies acumuladas: **7 en 16 dias** (0,44/dia). El embudo
  es 129 horas publicadas por CAMS ADS -> 40 con el fotometro en noche
  astronomica -> 7 sin Luna.

Simulando con la distribucion real de AOD y ese ruido, recuperar H_a = 1700 con
un intervalo del 68 % de +/-16 % pide **~100.000 horas**: seiscientos anios al
ritmo actual. Con 1.000 horas -- seis anios -- el intervalo sigue siendo
[562, 3746] m, que es no saber nada.

## El estimador tiene sesgo de muestra pequena, y esta medido

`f = c1/(c1+c2)` es un COCIENTE, y despues pasa por `-dh/ln(f)`: los dos pasos
son no lineales, asi que la media no se conserva. Medido sobre 60
realizaciones sinteticas con la verdad en 1700 m:

    n=200,  ruido 0,05 -> mediana 1653 (-2,8 %),  IC68 del 34 %
    n=800,  ruido 0,05 -> mediana 1720 (+1,2 %),  IC68 del 17 %
    n=3000, ruido 0,05 -> mediana 1706 (+0,4 %),  IC68 del  9 %
    n=200,  ruido 0,24 -> mediana 1358 (-20 %),   IC68 del 110 %

Se encoge con n, y en cualquier caso real queda muy por debajo de la anchura
del intervalo. No se corrige: escribirlo es mas util que taparlo con un ajuste
que habria que volver a validar.

## Lo que si lo fijaria

Un observable con B CONOCIDA. La extincion estelar medida (`k_extinction`) lo
es: B = 1,086 mag por unidad de profundidad optica, por definicion. Con B fija,
f se despeja de `aod_total` solo, sin necesitar especies ni romper ninguna
colinealidad, y basta una noche con estrellas a varias masas de aire.

Esa columna esta **VACIA**: 0 de 3.765.260 filas. Es el hueco que SPEC v8 1.1
ya declara. Mientras siga vacia, `AEROSOL_SCALE_HEIGHT_M` es una constante
supuesta, y lo honrado es que viaje diciendolo -- ver
`metodo/constante-plausible-esconde-que-es-constante` en CabraBase.

El observable entra por parametro justamente para eso: el dia que haya k
medida, este mismo estimador la acepta sin tocar una linea.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Minimo de horas para intentarlo siquiera. No garantiza nada -- la simulacion
# dice que con 1.000 el intervalo sigue siendo inutil -- pero por debajo de esto
# ni el arranque tiene sentido.
N_MINIMO = 50
# Por encima de esta correlacion entre los dos regresores, separarlos es
# aritmetica de ruido. Medido hoy: 0,957.
COLINEALIDAD_MAXIMA = 0.90
# H_a fuera de esto no es una altura de escala atmosferica, es un ajuste roto.
H_PLAUSIBLE = (200.0, 20000.0)
REMUESTREOS = 400


@dataclass
class AjusteAltura:
    """El resultado, que puede ser perfectamente 'no se puede'."""
    medido: bool
    n: int
    scale_height_m: float | None = None
    intervalo_68: tuple[float, float] | None = None
    porque: str | None = None
    diagnostico: dict = field(default_factory=dict)

    def __str__(self) -> str:
        if not self.medido:
            return f"H_a NO medido ({self.n} horas): {self.porque}"
        bajo, alto = self.intervalo_68
        return (f"H_a = {self.scale_height_m:.0f} m "
                f"[{bajo:.0f}, {alto:.0f}] con {self.n} horas")


def _f_desde(observable: np.ndarray, aod_total: np.ndarray,
             aod_dust: np.ndarray) -> float | None:
    """La fraccion de columna que queda sobre el sitio, de una regresion.

        Y = a + c1*aod_total + c2*aod_dust,  con  c1 = B*f  y  c2 = B*(1-f)

    asi que `f = c1/(c1+c2)` y B se va. Por eso hacen falta las especies: sin
    `aod_dust` no hay c2, y f se queda pegada a B para siempre.
    """
    X = np.column_stack([np.ones(len(aod_total)), aod_total, aod_dust])
    try:
        c = np.linalg.lstsq(X, observable, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    c1, c2 = float(c[1]), float(c[2])
    if c1 <= 0 or (c1 + c2) <= 0:
        return None                      # el ajuste da un perfil que crece con la altura
    f = c1 / (c1 + c2)
    return f if 0.0 < f < 1.0 else None


def fit_scale_height(observable, aod_total, aod_dust, delta_h_m: float, *,
                     n_minimo: int = N_MINIMO,
                     colinealidad_maxima: float = COLINEALIDAD_MAXIMA,
                     remuestreos: int = REMUESTREOS,
                     semilla: int = 0) -> AjusteAltura:
    """Estima H_a, o explica por que no se puede con estos datos.

    `observable` es cualquier magnitud que crezca linealmente con la profundidad
    optica vista desde el sitio: el brillo de cielo del fotometro, o -- mejor --
    la extincion medida, que ademas trae su B conocida.
    """
    Y = np.asarray(observable, float)
    T = np.asarray(aod_total, float)
    D = np.asarray([np.nan if d is None else d for d in aod_dust], float)

    util = np.isfinite(Y) & np.isfinite(T) & np.isfinite(D)
    n = int(util.sum())
    diagnostico: dict = {"n_con_especies": n, "n_total": len(Y)}

    # EL PRIMER FILTRO ES EL QUE IMPORTA. Sin especies no es que salga mal:
    # es que no existe respuesta, y un ajuste sin este aviso devolveria un
    # numero con toda la cara de haber medido algo.
    if n == 0:
        return AjusteAltura(
            medido=False, n=0, diagnostico=diagnostico,
            porque="sin aod_dust no hay dos regresores: H_a es indeterminable "
                   "por construccion, cualquier valor ajusta igual de bien")
    if n < n_minimo:
        return AjusteAltura(
            medido=False, n=n, diagnostico=diagnostico,
            porque=f"solo {n} horas con especies, hacen falta {n_minimo} para intentarlo")

    Y, T, D = Y[util], T[util], D[util]
    D = np.minimum(D, T)                 # el polvo es parte del total, no un extra
    colineal = float(abs(np.corrcoef(T, D)[0, 1]))
    ruido = float(np.std(Y - np.polyval(np.polyfit(T, Y, 1), T)))
    senal = float(abs(np.polyfit(T, Y, 1)[0]) * T.std())
    diagnostico.update({"colinealidad": round(colineal, 4),
                        "ruido": round(ruido, 4),
                        "senal": round(senal, 4),
                        "senal_ruido": round(senal / ruido, 4) if ruido else None})

    if colineal > colinealidad_maxima:
        return AjusteAltura(
            medido=False, n=n, diagnostico=diagnostico,
            porque=f"aod_dust y aod_total van a r={colineal:.3f}: separar sus "
                   f"coeficientes con esta colinealidad es aritmetica de ruido")

    f = _f_desde(Y, T, D)
    if f is None:
        return AjusteAltura(
            medido=False, n=n, diagnostico=diagnostico,
            porque="la regresion no da una fraccion entre 0 y 1: estos datos no "
                   "sostienen un perfil que decaiga con la altura")

    altura = -delta_h_m / math.log(f)
    if not H_PLAUSIBLE[0] <= altura <= H_PLAUSIBLE[1]:
        return AjusteAltura(
            medido=False, n=n, diagnostico=diagnostico,
            porque=f"H_a sale {altura:.0f} m, fuera de {H_PLAUSIBLE}: no es una "
                   f"altura de escala, es un ajuste roto")

    # Intervalo por remuestreo, no por formula: la no linealidad del paso
    # f -> -dh/ln(f) hace que propagar la sigma de la regresion mienta.
    rng = np.random.default_rng(semilla)
    alturas = []
    for _ in range(remuestreos):
        k = rng.integers(0, n, n)
        fk = _f_desde(Y[k], T[k], D[k])
        if fk is not None:
            h = -delta_h_m / math.log(fk)
            if H_PLAUSIBLE[0] <= h <= H_PLAUSIBLE[1]:
                alturas.append(h)
    diagnostico["remuestreos_validos"] = len(alturas)
    if len(alturas) < remuestreos // 4:
        return AjusteAltura(
            medido=False, n=n, diagnostico=diagnostico,
            porque=f"solo {len(alturas)} de {remuestreos} remuestreos dan una "
                   f"altura plausible: el ajuste no es estable")

    return AjusteAltura(medido=True, n=n, scale_height_m=altura,
                        intervalo_68=(float(np.percentile(alturas, 16)),
                                      float(np.percentile(alturas, 84))),
                        diagnostico=diagnostico)


# --- contra el archivo ------------------------------------------------------

def horas_limpias(root: Path | None = None):
    """Horas de fotometro utilizables: noche astronomica, sin Luna, lectura posible.

    Es la misma definicion con la que se mide el suelo del cielo
    (`skybackground.fit_epoch_floor`), a proposito: si se cambiara aqui, esto
    mediria sobre una poblacion distinta de la que calibra el resto.
    """
    from datetime import timezone
    from .ephem import moon_positions, sun_positions
    from .queries import connect
    from .site import NERPIO

    con = connect(root)
    filas = con.execute("""
        SELECT date_trunc('hour', t_utc) AS h, median(sky_mag_raw) AS m
        FROM measurements
        WHERE instrument_type = 'TESS' AND sky_mag_raw IS NOT NULL
        GROUP BY 1 HAVING count(*) >= 20 ORDER BY 1
    """).fetchall()
    if not filas:
        return [], [], {}, {}
    tiempos = [f[0].replace(tzinfo=timezone.utc) for f in filas]
    mags = [float(f[1]) for f in filas]
    soles = sun_positions(tiempos, NERPIO)
    lunas = moon_positions(tiempos, NERPIO)
    limpias = [(t, m) for t, m, s, l in zip(tiempos, mags, soles, lunas)
               if s.altitude < -18.0 and l.altitude <= -6.0 and 18.0 < m < 22.6]

    def _serie(modelo: str, columna: str) -> dict:
        return dict(con.execute(f"""
            WITH r AS (SELECT valid_utc, {columna},
                              row_number() OVER (PARTITION BY valid_utc
                                                 ORDER BY lead_h, run_utc DESC) rn
                       FROM forecasts
                       WHERE model = '{modelo}' AND {columna} IS NOT NULL)
            SELECT valid_utc, {columna} FROM r WHERE rn = 1""").fetchall())

    return ([t for t, _ in limpias], [m for _, m in limpias],
            _serie("cams_openmeteo", "aod_total"), _serie("cams_ads", "aod_dust"))


def medir(root: Path | None = None) -> AjusteAltura:
    """El intento completo contra el archivo, con su veredicto."""
    from .site import NERPIO

    tiempos, mags, aod, polvo = horas_limpias(root)
    if not tiempos:
        return AjusteAltura(medido=False, n=0,
                            porque="sin horas de fotometro en el archivo")
    con_aod = [(t, m) for t, m in zip(tiempos, mags) if t in aod]
    return fit_scale_height(
        [m for _, m in con_aod],
        [aod[t] for t, _ in con_aod],
        [polvo.get(t) for t, _ in con_aod],
        NERPIO.delta_h,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archivo", type=Path, default=None,
                        help="raiz del archivo (por defecto, data/ del repo)")
    args = parser.parse_args()
    ajuste = medir(args.archivo)
    print("[aerosolfit]", ajuste)
    for clave, valor in sorted(ajuste.diagnostico.items()):
        print(f"    {clave} = {valor}")


if __name__ == "__main__":
    main()
