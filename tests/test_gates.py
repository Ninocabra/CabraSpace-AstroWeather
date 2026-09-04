"""Tests for the safety gates and the night report (SPEC v8 1.1 and 1.4)."""

import datetime as dt

import pytest

from astroweather.gates import (
    GUST_CLOSE, evaluate, opening_probability, probability_usable,
)

NOW = dt.datetime(2026, 8, 27, 23, tzinfo=dt.timezone.utc)


def test_rain_on_the_sensor_closes_the_roof_whatever_the_forecast_says():
    verdict = evaluate(t_utc=NOW, hours_ahead=0.5, observed_rain=True,
                       gust=1.0, precipitation=0.0, precipitation_probability=0.0)
    assert not verdict.safe_to_open
    assert "sensor local" in verdict.summary


def test_a_gust_above_the_closure_limit_blocks():
    assert not evaluate(t_utc=NOW, hours_ahead=3, gust=GUST_CLOSE + 1).safe_to_open
    assert evaluate(t_utc=NOW, hours_ahead=3, gust=2.0).safe_to_open


def test_moderate_wind_warns_without_closing():
    verdict = evaluate(t_utc=NOW, hours_ahead=3, gust=9.0)
    assert verdict.safe_to_open
    assert any(r.level == "watch" for r in verdict.risks)
    assert "guiado" in verdict.summary


def test_cape_alone_never_closes_the_dome():
    """CAPE says the atmosphere could convect, not that anything is falling.

    Gating on it shuts the roof on plenty of perfectly usable nights.
    """
    verdict = evaluate(t_utc=NOW, hours_ahead=12, cape=2500.0,
                       precipitation_probability=5.0, gust=2.0)
    assert verdict.safe_to_open
    assert any(r.name == "tormenta" and r.level == "watch" for r in verdict.risks)


def test_cape_with_precipitation_does_close():
    verdict = evaluate(t_utc=NOW, hours_ahead=12, cape=2500.0,
                       precipitation_probability=60.0, gust=2.0)
    assert not verdict.safe_to_open


def test_rain_probability_decides_close_in_and_only_warns_far_out():
    """The same 60 % means different things at 3 hours and at 4 days."""
    soon = evaluate(t_utc=NOW, hours_ahead=3, precipitation_probability=60.0, gust=2.0)
    later = evaluate(t_utc=NOW, hours_ahead=90, precipitation_probability=60.0, gust=2.0)
    assert not soon.safe_to_open
    assert later.safe_to_open


def test_every_verdict_carries_its_reason():
    """A number without its why is not published anywhere in this system."""
    verdict = evaluate(t_utc=NOW, hours_ahead=2, gust=GUST_CLOSE + 2)
    assert verdict.summary.startswith("NO abrir:")
    assert all(risk.reason for risk in verdict.risks)


def test_a_blocked_hour_is_unusable_however_clear_it_is():
    blocked = evaluate(t_utc=NOW, hours_ahead=1, observed_rain=True)
    assert probability_usable(0.99, blocked) == 0.0


def test_watched_risks_eat_into_the_odds_without_zeroing_them():
    windy = evaluate(t_utc=NOW, hours_ahead=3, gust=9.0)
    assert 0 < probability_usable(0.8, windy) < 0.8


def test_opening_probability_falls_back_to_climatology_without_data():
    assert opening_probability([], climatology=0.57) == pytest.approx(0.57)


def test_a_night_of_good_hours_is_worth_opening_for():
    assert opening_probability([0.8] * 8) > 0.7
    assert opening_probability([0.1, 0.1]) < 0.2


# --- la ventana de dos horas (2026-09-04) ------------------------------------
# Antes la noche valia lo que su MEJOR hora en cuanto sumaba 4 h esperadas, y
# con una curva que nunca bajaba de 0,49 eso era siempre: 74 veredictos
# publicados, cero "Cerrado". Ahora vale la hora mas floja de la mejor ventana
# de dos horas seguidas.

from astroweather.gates import opening_window  # noqa: E402


def test_una_sola_hora_buena_no_abre_una_noche():
    """La noche real del 04-09-2026: 100 % de nubes de 21 a 23 UTC y una hora
    limpia a las 03. Con la regla vieja salia 0,92; ahora manda la pareja."""
    hours = [0.69, 0.77, 0.56, 0.49, 0.49, 0.62, 0.70, 0.81, 0.89, 0.92, 0.88, 0.74, 0.60]
    assert opening_probability(hours) == pytest.approx(0.89)   # min(0.89, 0.92)
    lonely = [0.1] * 8 + [0.95] + [0.1] * 4
    assert opening_probability(lonely) == pytest.approx(0.1)
    assert opening_window(lonely)[2] == pytest.approx(0.1)


def test_la_ventana_dice_donde_se_apoya_el_veredicto():
    hours = [0.2, 0.3, 0.7, 0.8, 0.4, 0.9, 0.35]
    first, last, floor = opening_window(hours)
    assert (first, last) == (2, 3)
    assert floor == pytest.approx(0.7)


def test_la_ventana_se_mide_en_horas_y_no_en_casillas():
    """Con rejilla de media hora, dos horas son cuatro casillas."""
    hours = [0.1, 0.9, 0.9, 0.1, 0.6, 0.6, 0.6, 0.6, 0.1]
    assert opening_probability(hours, step_hours=1.0) == pytest.approx(0.9)
    assert opening_probability(hours, step_hours=0.5) == pytest.approx(0.6)


def test_una_noche_mas_corta_que_la_ventana_vale_su_hora_mas_floja():
    assert opening_probability([0.7]) == pytest.approx(0.7)
    assert opening_window([]) is None


def test_control_positivo_la_regla_vieja_si_abria_esa_noche():
    """La regla anterior, reconstruida: si este test no discriminara, el de
    arriba tampoco. Misma noche, 0,92."""
    hours = [0.69, 0.77, 0.56, 0.49, 0.49, 0.62, 0.70, 0.81, 0.89, 0.92, 0.88, 0.74, 0.60]
    old_rule = max(hours) if sum(hours) >= 4.0 else min(1.0, sum(hours) / 4.0) * max(hours)
    assert old_rule == pytest.approx(0.92)
    assert opening_probability(hours) < old_rule
