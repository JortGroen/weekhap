"""Deterministische parsertests op de opgeslagen WordPress-fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.normalize_kistje import NormalizeError, validate_payload
from src.parse_kistje import ParseError, parse_content, quality_warnings

FIXTURE = Path(__file__).parent / "fixtures" / "wordpress_response.json"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sections(payload):
    return parse_content(payload["content"]["rendered"])


def _by_week(sections, week_number):
    matches = [section for section in sections if section.week_number == week_number]
    assert matches, "week %d niet gevonden" % week_number
    return matches[0]


def test_beide_weken_worden_gevonden(sections):
    assert [section.week_number for section in sections] == [35, 36]


def test_datumranges_worden_gevonden(sections):
    assert _by_week(sections, 35).date_range_text == "24 t/m 30 aug"
    assert _by_week(sections, 36).date_range_text == "31 aug t/m 6 sept"


def test_groente_en_fruit_worden_gescheiden(sections):
    for week_number in (35, 36):
        section = _by_week(sections, week_number)
        assert len(section.vegetables) == 4
        assert len(section.fruit) == 4
        assert all(p.category == "vegetable" for p in section.vegetables)
        assert all(p.category == "fruit" for p in section.fruit)


def test_producten_staan_in_de_juiste_categorie(sections):
    week35 = _by_week(sections, 35)
    assert [p.name for p in week35.vegetables] == [
        "Snijbiet",
        "Knolselderij met loof",
        "Courgette",
        "Regenboogwortel",
    ]
    assert [p.name for p in week35.fruit] == [
        "Beurre hardy peer",
        "Jubileum pruim",
        "Bananen",
        "Kiwibes",
    ]


def test_ster_wordt_gedetecteerd(sections):
    week35 = _by_week(sections, 35)
    excluded = [
        p.name
        for p in week35.vegetables + week35.fruit
        if p.excluded_from_one_person_box
    ]
    assert excluded == ["Regenboogwortel", "Kiwibes"]


def test_ster_producten_blijven_in_het_2_persoonskistje(sections):
    """Producten met een * zitten niet in het 1-persoonskistje, wel in het 2-persoons."""
    for section in sections:
        for product in section.vegetables + section.fruit:
            assert product.as_dict()["included_in_two_person_box"] is True


def test_ster_zit_niet_in_de_productnaam(sections):
    for section in sections:
        for product in section.vegetables + section.fruit:
            assert "*" not in product.name


def test_oorsprong_is_geen_onderdeel_van_de_productnaam(sections):
    week35 = _by_week(sections, 35)
    wortel = next(p for p in week35.vegetables if p.name == "Regenboogwortel")
    assert wortel.name == "Regenboogwortel"
    assert wortel.origin == "Familie Hospers uit de Noordoostpolder"
    assert "Hospers" not in wortel.name
    assert "Biologisch" not in wortel.origin


def test_certificering_wordt_herkend(sections):
    week35 = _by_week(sections, 35)
    assert all(
        p.certification == "biologisch" for p in week35.vegetables + week35.fruit
    )

    week36 = _by_week(sections, 36)
    paprika = next(p for p in week36.vegetables if p.name == "Puntpaprika")
    assert paprika.certification == "biodynamisch"
    tomaatjes = next(p for p in week36.fruit if p.name == "WildWonder tomaatjes")
    assert tomaatjes.certification == "biodynamisch"


def test_volledige_bronregel_blijft_bewaard(sections):
    week35 = _by_week(sections, 35)
    wortel = next(p for p in week35.vegetables if p.name == "Regenboogwortel")
    assert wortel.source_line == (
        "Regenboogwortel van Familie Hospers uit de Noordoostpolder (Biologisch) *"
    )


def test_parser_verwerkt_html_entities_en_shortcodes(sections):
    """De bron bevat &amp;, typografische quotes en Visual Composer-shortcodes."""
    week36 = _by_week(sections, 36)
    pompoen = next(p for p in week36.vegetables if p.name == "Oranje pompoen")
    assert "&" in pompoen.origin
    assert "&amp;" not in pompoen.origin
    assert "[vc_" not in pompoen.origin
    assert "vc_column_text" not in pompoen.name


def test_geen_kwaliteitswaarschuwingen_op_de_fixture(sections):
    assert quality_warnings(sections) == []


# --- Foutpaden -------------------------------------------------------------


def test_lege_content_wordt_afgewezen():
    with pytest.raises(ParseError):
        parse_content("")
    with pytest.raises(ParseError):
        parse_content("   \n  ")


def test_ontbrekende_week_geeft_expliciete_fout():
    with pytest.raises(ParseError, match="Geen enkele weeksectie"):
        parse_content("<div><p>Binnenkort meer nieuws over je kistje.</p></div>")


def test_week_zonder_fruit_wordt_afgewezen():
    html = (
        "<h3><strong>Week 12</strong></h3><h3>16 t/m 22 mrt</h3>"
        "<div> Kistjes Groente:<ul><li><b>Prei</b> uit eigen tuin</li></ul></div>"
    )
    with pytest.raises(ParseError, match="geen fruitproducten"):
        parse_content(html)


def test_week_zonder_groente_wordt_afgewezen():
    html = (
        "<h3><strong>Week 12</strong></h3><h3>16 t/m 22 mrt</h3>"
        "<div> Kistje Fruit:<ul><li><b>Appel</b> uit Betuwe</li></ul></div>"
    )
    with pytest.raises(ParseError, match="geen groenteproducten"):
        parse_content(html)


def test_dubbel_weeknummer_wordt_afgewezen():
    section = (
        "<h3><strong>Week 12</strong></h3><h3>16 t/m 22 mrt</h3>"
        "<div> Kistjes Groente:<ul><li><b>Prei</b> uit eigen tuin</li></ul></div>"
        "<div> Kistje Fruit:<ul><li><b>Appel</b> uit Betuwe</li></ul></div>"
    )
    with pytest.raises(ParseError, match="Dubbel weeknummer"):
        parse_content(section + section)


def test_afwijkend_aantal_producten_geeft_warning_maar_geen_fout():
    html = (
        "<h3><strong>Week 12</strong></h3><h3>16 t/m 22 mrt</h3>"
        "<div> Kistjes Groente:<ul><li><b>Prei</b> uit eigen tuin</li></ul></div>"
        "<div> Kistje Fruit:<ul><li><b>Appel</b> uit Betuwe</li></ul></div>"
    )
    sections = parse_content(html)
    warnings = quality_warnings(sections)
    assert len(sections) == 1
    assert any("groenten" in warning for warning in warnings)
    assert any("fruitsoorten" in warning for warning in warnings)


# --- Responsevalidatie -----------------------------------------------------


def test_fixture_voldoet_aan_responsevalidatie(payload):
    validate_payload(payload)


def test_verkeerd_page_id_wordt_afgewezen(payload):
    broken = dict(payload)
    broken["id"] = 12345
    with pytest.raises(NormalizeError, match="page id"):
        validate_payload(broken)


def test_verkeerde_slug_wordt_afgewezen(payload):
    broken = dict(payload)
    broken["slug"] = "iets-anders"
    with pytest.raises(NormalizeError, match="slug"):
        validate_payload(broken)


def test_onparseerbare_modified_wordt_afgewezen(payload):
    broken = dict(payload)
    broken["modified"] = "gisteren"
    with pytest.raises(NormalizeError, match="niet parseerbaar"):
        validate_payload(broken)


def test_lege_content_in_response_wordt_afgewezen(payload):
    broken = dict(payload)
    broken["content"] = {"rendered": ""}
    with pytest.raises(NormalizeError, match="leeg"):
        validate_payload(broken)
