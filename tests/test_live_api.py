"""Live integratietest tegen de Hoeve Biesland WordPress REST API.

Geen fixture: dit doet een echte HTTPS-request naar de bron en draait de
volledige verwerking tot en met genormaliseerde JSON in het geheugen. Er wordt
niets naar docs/ geschreven.

Deze test bewaakt iets wat fixtures per definitie niet kunnen: dat de *huidige*
bron nog steeds parseerbaar is. Wijzigt Hoeve Biesland de opmaak, dan valt die
hier om, niet pas in productie.

Draaien:
    python -m pytest tests/test_live_api.py -v -s
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.fetch_kistje import (
    API_URL,
    PAGE_ID,
    FetchBlocked,
    FetchError,
    fetch_with_fallback,
)
from src.normalize_kistje import (
    PAGE_SLUG,
    build_latest,
    build_status,
    build_week_documents,
    validate_payload,
)
from src.parse_kistje import parse_content, quality_warnings

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_payload() -> dict:
    try:
        return fetch_with_fallback()
    except FetchBlocked as exc:
        # Een actieve weigering is een echte bevinding, geen storing:
        # stilzwijgend skippen zou de blokkade onzichtbaar maken.
        pytest.fail("De bron weigert deze runner: %s" % exc)
    except FetchError as exc:
        pytest.skip("Hoeve Biesland tijdelijk niet bereikbaar: %s" % exc)


@pytest.fixture(scope="module")
def live_sections(live_payload):
    return parse_content(live_payload["content"]["rendered"])


def test_live_response_heeft_verwachte_identiteit(live_payload):
    assert live_payload["id"] == PAGE_ID
    assert live_payload["slug"] == PAGE_SLUG
    print("\nBron           : %s" % API_URL)
    print("id / slug      : %s / %s" % (live_payload["id"], live_payload["slug"]))


def test_live_modified_is_parseerbaar(live_payload):
    modified = live_payload.get("modified")
    assert modified
    parsed = datetime.fromisoformat(modified)
    assert parsed.year >= 2023
    print("modified       : %s" % modified)


def test_live_response_doorstaat_validatie(live_payload):
    validate_payload(live_payload)


def test_live_content_is_niet_leeg(live_payload):
    content = live_payload["content"]["rendered"]
    assert content.strip()
    print("content.rendered: %d tekens" % len(content))


def test_live_minstens_een_geldige_week(live_sections):
    assert live_sections, "geen enkele week geparsed uit de live bron"
    for section in live_sections:
        assert 1 <= section.week_number <= 53
        assert section.date_range_text
        assert section.vegetables
        assert section.fruit
        assert all(product.name for product in section.vegetables + section.fruit)

    print(
        "weken gevonden : %s"
        % ", ".join(
            "Week %d (%s)" % (s.week_number, s.date_range_text) for s in live_sections
        )
    )


def test_live_producten_zijn_bruikbaar(live_sections):
    for section in live_sections:
        print(
            "  Week %d groente: %s"
            % (section.week_number, ", ".join(p.name for p in section.vegetables))
        )
        print(
            "  Week %d fruit  : %s"
            % (section.week_number, ", ".join(p.name for p in section.fruit))
        )
        for product in section.vegetables + section.fruit:
            # De naam mag geen herkomst of certificering bevatten.
            assert "(" not in product.name
            assert "*" not in product.name


def test_live_kwaliteitswaarschuwingen_zijn_zichtbaar(live_sections):
    """Afwijkend aantal producten is geen fout, maar moet wel opvallen."""
    warnings = quality_warnings(live_sections)
    if warnings:
        print("waarschuwingen : %s" % "; ".join(warnings))
    else:
        print("waarschuwingen : geen")


def test_live_normalisatie_levert_geldig_schema(live_payload, live_sections):
    """Genereer de publicatie in het geheugen en controleer het schema."""
    documents = build_week_documents(live_payload, live_sections)
    assert documents

    for key, document in documents.items():
        assert document["schema_version"] == 1
        assert document["week"] == key
        assert key.startswith(str(document["iso_year"]))
        assert "W%02d" % document["week_number"] in key
        assert document["source"]["page_id"] == PAGE_ID
        assert document["source"]["modified"]
        assert document["source"]["fetched_at"]
        assert document["date_range_text"]
        assert document["vegetables"] and document["fruit"]

        eggs = [
            item
            for item in document["planner_additions"]
            if item["name"] == "Eieren"
        ]
        assert len(eggs) == 1 and eggs[0]["quantity"] == 6

    print("weeksleutels   : %s" % ", ".join(sorted(documents)))


def test_live_latest_en_status_zijn_consistent(live_payload, live_sections):
    fetched_at = datetime.now().astimezone()
    documents = build_week_documents(live_payload, live_sections, fetched_at=fetched_at)

    latest = build_latest(live_payload, documents, fetched_at)
    status = build_status(live_payload, documents, fetched_at, [])

    assert latest["published_weeks"] == sorted(documents)
    assert status["published_weeks"] == sorted(documents)
    assert status["status"] == "ok"
    assert set(latest["weeks"]) == set(documents)


def test_live_weeksleutels_zijn_uniek(live_payload, live_sections):
    documents = build_week_documents(live_payload, live_sections)
    assert len(documents) == len(live_sections)
