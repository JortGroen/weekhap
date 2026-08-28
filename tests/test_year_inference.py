"""Tests voor ISO-jaarinferentie en het parsen van de datumrange.

De bron noemt alleen 'Week 35' en nooit het jaar. Deze tests bewaken dat het
jaar uit de datumrange en `modified` wordt afgeleid, en niet uit het huidige
kalenderjaar -- rond de jaarwisseling levert dat stil de verkeerde week op.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.normalize_kistje import infer_iso_year, parse_date_range


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
