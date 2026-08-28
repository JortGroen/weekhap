"""Echte end-to-end test tegen de publieke GitHub Pages-URL. Geen fixture.

Deze test doet een daadwerkelijke HTTPS-request naar het publieke eindpunt en
bewijst dat de maaltijdplanner de kistinhoud zonder menselijke tussenkomst kan
ophalen en gebruiken.

Draaien:
    python -m pytest tests/test_live_e2e.py -v -s

Overslaan (bijvoorbeeld offline):
    python -m pytest -m "not live"
"""

from __future__ import annotations

from datetime import date

import pytest

from src.kistje_client import (
    DEFAULT_BASE_URL,
    KistjeUnavailable,
    build_url,
    fetch_week,
    get_box_for_plan_start,
    week_key_for_plan_start,
)

pytestmark = pytest.mark.live

# Een bekende, gepubliceerde week. Bewust expliciet: deze test moet ook slagen
# als de bron nog niet is doorgerold naar een nieuwe week.
KNOWN_WEEK = "2026-W35"
KNOWN_PLAN_START = date(2026, 8, 31)  # maandag -> donderdag 27 aug -> 2026-W35


@pytest.fixture(scope="module")
def live_box():
    """Haal de bekende week live op; sla de test over als de bron onbereikbaar is."""
    try:
        return fetch_week(KNOWN_WEEK)
    except KistjeUnavailable as exc:
        pytest.skip("Publieke bron niet bereikbaar: %s" % exc)


def test_url_wijst_naar_de_publieke_pages_bron():
    url = build_url(KNOWN_WEEK)
    assert url == (
        "https://jortgroen.github.io/weekhap/api/by-week/%s.json" % KNOWN_WEEK
    )
    assert url.startswith("https://")
    assert DEFAULT_BASE_URL.startswith("https://jortgroen.github.io/weekhap/api/")


def test_live_ophalen_geeft_de_gevraagde_week(live_box):
    assert live_box.week == KNOWN_WEEK
    assert live_box.week_number == 35
    print("\nOpgehaalde URL : %s" % live_box.url)
    print("Week           : %s (%s)" % (live_box.week, live_box.date_range_text))


def test_live_bron_metadata_is_aanwezig(live_box):
    assert live_box.source.get("page_id") == 19172
    assert live_box.source.get("modified")
    assert live_box.source.get("fetched_at")
    print("Bron gewijzigd : %s" % live_box.source["modified"])


def test_live_groente_en_fruit_zijn_bruikbaar(live_box):
    assert live_box.vegetables, "geen groente gevonden"
    assert live_box.fruit, "geen fruit gevonden"
    assert all(item["name"] for item in live_box.produce)

    print("Groente        : %s" % ", ".join(i["name"] for i in live_box.vegetables))
    print("Fruit          : %s" % ", ".join(i["name"] for i in live_box.fruit))


def test_live_tweepersoonskistje_bevat_ook_de_ster_producten(live_box):
    assert all(item["included_in_two_person_box"] for item in live_box.produce)
    starred = [
        item["name"]
        for item in live_box.produce
        if item.get("excluded_from_one_person_box")
    ]
    print("Met * (wel in 2-pers kistje): %s" % ", ".join(starred))


def test_live_zes_eieren_zijn_toegevoegd(live_box):
    eggs = [item for item in live_box.planner_additions if item["name"] == "Eieren"]
    assert len(eggs) == 1
    assert eggs[0]["quantity"] == 6
    assert eggs[0]["unit"] == "stuks"
    print("Plannerregel   : %d %s eieren" % (eggs[0]["quantity"], eggs[0]["unit"]))


def test_live_volledige_flow_vanaf_planstart_zonder_tussenkomst():
    """Van planstart tot bruikbare boodschappenlijst, zonder handmatige input."""
    assert week_key_for_plan_start(KNOWN_PLAN_START) == KNOWN_WEEK

    try:
        box = get_box_for_plan_start(KNOWN_PLAN_START)
    except KistjeUnavailable as exc:
        pytest.skip("Publieke bron niet bereikbaar: %s" % exc)

    items = box.shopping_items()
    names = [item["name"] for item in items]

    assert len(box.produce) >= 2
    assert "Eieren" in names
    assert len(items) == len(box.produce) + len(box.planner_additions)

    print("\nPlanstart      : %s" % KNOWN_PLAN_START)
    print("Boodschappen   : %s" % ", ".join(names))


def test_live_ontbrekende_week_geeft_expliciete_fout():
    """Een niet-gepubliceerde week mag nooit stilzwijgend iets anders opleveren."""
    with pytest.raises(KistjeUnavailable, match="404"):
        fetch_week("1999-W01", backoffs=(), sleep=lambda _: None)
