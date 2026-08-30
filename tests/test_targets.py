"""Tests for the SNR-hours engine (SPEC v8 section 1.1)."""

import datetime as dt

import pytest

from astroweather.ephem import moon_positions, night_for
from astroweather.seeing import integrate
from astroweather.targets import (
    CATALOGUE, MOON_FILTER_FACTOR, SKY_GATE, Target, evaluate_night, hourly_yield,
)

FULL_MOON = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
NEW_MOON = dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc)


def _mejor_por_clase(resultados):
    """El MEJOR objeto de cada clase, no el ultimo que pase por el diccionario.

    Un `{r.difficulty: r.q}` se queda con el ultimo, y desde que el catalogo
    paso de 10 a 44 objetos (29-08-2026) el ultimo de cada clase es el peor:
    cualquier objeto de invierno esta bajo el horizonte en agosto y vale cero.
    Lo que estos tests afirman es "esta clase RINDE esta noche", y eso lo dice
    su mejor representante, no uno cualquiera.
    """
    mejores = {}
    for r in resultados:
        clase = r.target.difficulty
        mejores[clase] = max(mejores.get(clase, 0.0), r.q_conditional)
    return mejores


def _run(day, probability=0.8):
    night = night_for(day)
    times = night.hours(60)
    return evaluate_night(CATALOGUE, times,
                          usable_probability=[probability] * len(times),
                          turbulence=integrate(20.0), aod_total=0.06, aod_dust=0.01)


def test_a_full_moon_night_is_a_narrowband_night():
    """The advice a good astrophotographer would give, derived from physics.

    Estuvo en xfail unas horas el 2026-08-28. Con la ley de dispersion lunar
    medida, el cielo con Luna llena alta es hasta 1,8 mag mas brillante de lo
    que decia el modelo, y el 0,12 por clase que el codigo usaba -- que no esta
    en SPEC -- dejaba de colar: el producto pasaba a decir que con Luna llena no
    se hace banda estrecha. Se arreglo adoptando los factores POR FILTRO de
    SPEC 2.3, que es el documento que manda.
    """
    results = _mejor_por_clase(_run(FULL_MOON))
    assert results["narrowband"] > 1.0
    assert results["extremo_contraste"] == 0.0     # IFN under a full Moon: no
    assert results["bajo_contraste"] == 0.0


def test_a_new_moon_night_favours_everything_else():
    """And the advice must invert, or the engine is just saying one thing."""
    mejores = _mejor_por_clase(_run(NEW_MOON))
    # Sin Luna lo que se abre es justo lo que la Luna cerraba: el contraste
    # extremo y el bajo contraste dejan de valer cero, que es la inversion que
    # el test persigue. Cual sea el PRIMERO del ranking depende de que hay en
    # el cielo esa noche concreta, y eso no es fisica del motor.
    assert mejores["extremo_contraste"] > 0.0
    assert mejores["bajo_contraste"] > 0.0
    assert max(mejores.values()) > 5.0


def test_the_moon_separation_is_what_kills_a_target_not_just_its_phase():
    """The directional model is the whole point: 14 degrees away is fatal."""
    results = {r.target.name: r for r in _run(FULL_MOON)}
    einstein = results["Cruz de Einstein (Q2237+0305)"]
    assert einstein.min_moon_separation < 20
    assert einstein.q_conditional == 0.0


def test_expected_hours_never_exceed_conditional_ones():
    """E[Q] is Q_cond weighted by the odds; it cannot be more optimistic."""
    for result in _run(NEW_MOON, probability=0.5):
        assert result.q_expected <= result.q_conditional + 1e-9


def test_certainty_is_worth_something():
    confident = _run(NEW_MOON, probability=0.95)[0]
    doubtful = _run(NEW_MOON, probability=0.20)[0]
    assert confident.q_expected > doubtful.q_expected
    assert confident.q_conditional == pytest.approx(doubtful.q_conditional)


def test_only_compact_targets_care_about_seeing():
    """alpha is what stops the engine treating a nebula like a lens."""
    sharp = dict(airmass=1.0, sky_mag=21.7, k_total=0.18, difficulty="normal")
    extended_good = hourly_yield(seeing_arcsec=1.0, alpha=0.0, **sharp)
    extended_bad = hourly_yield(seeing_arcsec=4.0, alpha=0.0, **sharp)
    compact_good = hourly_yield(seeing_arcsec=1.0, alpha=1.0, **sharp)
    compact_bad = hourly_yield(seeing_arcsec=4.0, alpha=1.0, **sharp)

    assert extended_good == extended_bad          # blur does not touch surface brightness
    assert compact_good > compact_bad * 3         # but it is everything for a point


def test_an_impossible_hour_scores_zero_not_a_little():
    """Partial credit for an impossible hour is worse than none: it accumulates."""
    assert hourly_yield(airmass=1.0, sky_mag=19.0, seeing_arcsec=2.0,
                        k_total=0.18, alpha=0.0, difficulty="extremo_contraste") == 0.0
    assert hourly_yield(airmass=1.0, sky_mag=21.6, seeing_arcsec=2.0,
                        k_total=0.18, alpha=0.0, difficulty="extremo_contraste") > 0.0


def test_narrowband_survives_moonlight_by_construction():
    assert MOON_FILTER_FACTOR["narrowband"] < 0.2
    assert MOON_FILTER_FACTOR["normal"] == 1.0
    assert SKY_GATE["extremo_contraste"] > SKY_GATE["normal"]


def test_every_verdict_carries_its_reason():
    for result in _run(FULL_MOON):
        assert result.reason
        assert result.verdict in ("recomendado", "posible", "poco rentable",
                                  "no observable")


def test_the_answer_does_not_depend_on_how_finely_the_night_was_sampled():
    """La misma noche, tres rejillas: el veredicto tiene que ser el mismo.

    `evaluate_night` tomaba el paso como `times[1] - times[0]` y lo aplicaba a
    toda la noche. En una rejilla uniforme eso es un error de poste; en la
    ADAPTATIVA es un factor, porque `adaptive_hours` abre en crepusculo a 30
    minutos y luego baja a 10: cada muestra oscura contaba triple. Medido el
    2026-08-29 sobre la noche del 28-08, NGC 7000 daba 11,97 h de SNR en la
    cupula contra 4,66 en el exportador -- la misma noche, 2,7x.

    El margen es del 8 % y no del 1 % porque lo que queda es discretizacion
    real, no el peso: las rejillas cubren ventanas que difieren hasta 50
    minutos (`hours()` corta en `<= window_end`) y el rendimiento cambia
    deprisa en los bordes. Medido sobre 10 objetos y cuatro pasos, lo que
    queda entre rejillas finas es <= 5 %.
    """
    night = night_for(NEW_MOON)
    rejillas = [night.adaptive_hours(10, 30), night.hours(30), night.hours(15)]
    resultados = []
    for times in rejillas:
        resultados.append({
            r.target.name: r.q_conditional
            for r in evaluate_night(CATALOGUE, times,
                                    usable_probability=[1.0] * len(times),
                                    turbulence=integrate(20.0),
                                    aod_total=0.06, aod_dust=0.01)})

    # Solo los objetos con margen: cerca del corte de su clase el rendimiento
    # es una funcion escarpada del tiempo y la dispersion es fisica (el IFN de
    # M81 se mueve un 15 % entre rejillas finas, sobre un valor de 1,2).
    observables = [n for n, q in resultados[0].items() if q > 2.0]
    assert observables, "la noche de Luna nueva tiene que dar algo observable"
    for nombre in observables:
        valores = [r[nombre] for r in resultados]
        assert max(valores) <= 1.08 * min(valores), (
            f"{nombre}: la rejilla cambia el resultado -- {valores}")


def test_the_night_is_never_longer_than_itself():
    """Las horas que se reparten entre las muestras suman el vano, no mas.

    La comprobacion directa de lo anterior: sea cual sea el espaciado, los
    pesos del trapecio suman exactamente lo que dura la rejilla. Con el paso
    fijo antiguo, la adaptativa sumaba 29,00 h sobre un vano de 12,83.
    """
    from astroweather.targets import _sample_weights

    night = night_for(NEW_MOON)
    for times in (night.adaptive_hours(10, 30), night.hours(60), night.hours(5)):
        vano = (times[-1] - times[0]).total_seconds() / 3600
        assert sum(_sample_weights(times)) == pytest.approx(vano, abs=1e-9)


def test_separacion_a_la_luna_en_el_mismo_marco_que_la_altura():
    """La separacion tiene que salir de las MISMAS alturas y azimuts que se
    publican, no de otro marco de referencia.

    Hasta el 30-08-2026 salia de comparar el objeto en ICRS -- baricentrico --
    con la Luna en GCRS -- geocentrico -- y entre esos dos marcos hay una
    traslacion de una unidad astronomica: la Luna quedaba colocada donde se la
    ve desde el baricentro del sistema solar. El error llegaba a 38 grados, y
    con la ley de dispersion medida aqui (10^(-theta/48,55)) quince grados ya
    son un factor dos en el brillo lunar sobre el objeto.

    Se comprueba contra la geometria esferica pura sobre alt/az, que es
    exactamente lo que hace la cupula del navegador: si las dos cuentas no dan
    lo mismo, el panel y el motor discrepan sobre la misma noche.
    """
    import math

    when = [FULL_MOON.replace(hour=h) for h in (21, 23)] +            [FULL_MOON.replace(hour=2) + dt.timedelta(days=1)]
    luna = moon_positions(when)
    for objetivo in CATALOGUE[:12]:
        alt, az, sep = objetivo.positions(when)
        for i, m in enumerate(luna):
            a1, z1, a2, z2 = map(math.radians, (alt[i], az[i], m.altitude, m.azimuth))
            esperado = math.degrees(math.acos(max(-1.0, min(1.0,
                math.sin(a1) * math.sin(a2)
                + math.cos(a1) * math.cos(a2) * math.cos(z1 - z2)))))
            assert sep[i] == pytest.approx(esperado, abs=0.05), (
                f"{objetivo.name} a las {when[i]:%H:%M}: el motor dice "
                f"{sep[i]:.1f} grados y la geometria {esperado:.1f}")
