"""Tests voor ophaaldonderdag, ISO-weeksleutel en jaarinferentie."""

from __future__ import annotations

from datetime import date

import pytest

from src.normalize_kistje import infer_iso_year, parse_date_range
from src.planner_week import (
    iso_week_key,
    pickup_date_for_plan_start,
    week_key_for_plan_start,
)


# --- Verplichte testcases uit paragraaf 13 van het projectplan --------------


@pytest.mark.parametrize(
    "plan_start, expected_pickup, expected_key",
    [
        (date(2026, 8, 27), date(2026, 8, 27), "2026-W35"),  # planstart is donderdag
        (date(2026, 8, 31), date(2026, 8, 27), "2026-W35"),  # planstart is maandag
        (date(2026, 9, 3), date(2026, 9, 3), "2026-W36"),
    ],
)
def test_verplichte_plannercases(plan_start, expected_pickup, expected_key):
    assert pickup_date_for_plan_start(plan_start) == expected_pickup
    assert week_key_for_plan_start(plan_start) == expected_key


def test_planstart_donderdag_gebruikt_dezelfde_dag():
    plan_start = date(2026, 8, 27)
    assert plan_start.weekday() == 3
    assert pickup_date_for_plan_start(plan_start) == plan_start


@pytest.mark.parametrize(
    "plan_start, expected_pickup",
    [
        (date(2026, 8, 28), date(2026, 8, 27)),  # vrijdag -> donderdag ervoor
        (date(2026, 8, 29), date(2026, 8, 27)),  # zaterdag
        (date(2026, 8, 30), date(2026, 8, 27)),  # zondag
        (date(2026, 9, 1), date(2026, 8, 27)),   # dinsdag
        (date(2026, 9, 2), date(2026, 8, 27)),   # woensdag: donderdag ervoor
    ],
)
def test_meest_recente_donderdag_voor_alle_weekdagen(plan_start, expected_pickup):
    assert pickup_date_for_plan_start(plan_start) == expected_pickup


def test_pickup_ligt_nooit_in_de_toekomst():
    for offset in range(400):
        plan_start = date(2026, 1, 1).toordinal() + offset
        plan_start = date.fromordinal(plan_start)
        pickup = pickup_date_for_plan_start(plan_start)
        assert pickup <= plan_start
        assert (plan_start - pickup).days < 7
        assert pickup.weekday() == 3


# --- Jaarwisselingen -------------------------------------------------------


def test_iso_week_key_gebruikt_iso_jaar_niet_kalenderjaar():
    # 31 december 2025 valt in ISO-week 1 van 2026.
    day = date(2025, 12, 31)
    assert day.isocalendar()[0] == 2026
    assert iso_week_key(day) == "2026-W01"


def test_planstart_begin_januari_pakt_donderdag_in_vorig_kalenderjaar():
    # Maandag 5 januari 2026 -> donderdag 1 januari 2026, ISO-week 2026-W01.
    plan_start = date(2026, 1, 5)
    assert pickup_date_for_plan_start(plan_start) == date(2026, 1, 1)
    assert week_key_for_plan_start(plan_start) == "2026-W01"


def test_planstart_over_jaargrens_heen():
    # Maandag 29 december 2025 -> donderdag 25 december 2025 (ISO 2025-W52).
    plan_start = date(2025, 12, 29)
    assert pickup_date_for_plan_start(plan_start) == date(2025, 12, 25)
    assert week_key_for_plan_start(plan_start) == "2025-W52"


# --- Datumrange-parsing ----------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_day, expected_month",
    [
        ("24 t/m 30 aug", 24, 8),
        ("31 aug t/m 6 sept", 31, 8),
        ("1 t/m 7 december", 1, 12),
        ("29 t/m 4 sept", 29, 8),  # loopt over de maandgrens: start hoort bij augustus
        ("29 dec t/m 4 jan", 29, 12),
    ],
)
def test_parse_date_range(text, expected_day, expected_month):
    parsed = parse_date_range(text)
    assert parsed is not None
    assert (parsed.start_day, parsed.start_month) == (expected_day, expected_month)


def test_parse_date_range_zonder_bruikbare_tekst():
    assert parse_date_range("") is None
    assert parse_date_range("binnenkort bekend") is None


# --- Jaarinferentie --------------------------------------------------------


def test_jaarinferentie_uit_datumrange():
    # Week 35 met maandag 24 augustus hoort bij 2026.
    assert infer_iso_year(35, "24 t/m 30 aug", date(2026, 8, 27)) == 2026
    assert date.fromisocalendar(2026, 35, 1) == date(2026, 8, 24)


def test_jaarinferentie_kiest_niet_blind_het_kalenderjaar_van_modified():
    # Bron gewijzigd op 30 december 2025, maar publiceert week 1: dat is ISO 2026.
    assert infer_iso_year(1, "29 dec t/m 4 jan", date(2025, 12, 30)) == 2026


def test_jaarinferentie_december_week_bij_modified_in_januari():
    # Zou een naive implementatie het kalenderjaar van modified pakken, dan werd
    # dit 2026 in plaats van 2025.
    assert infer_iso_year(52, "22 t/m 28 dec", date(2026, 1, 2)) == 2025


def test_jaarinferentie_valt_terug_op_modified_zonder_datumrange():
    # Zonder datumrange wint de week die het dichtst bij modified ligt.
    assert infer_iso_year(35, "", date(2026, 8, 27)) == 2026


def test_jaarinferentie_negeert_onparseerbare_datumrange():
    assert infer_iso_year(35, "datum volgt", date(2026, 8, 27)) == 2026
