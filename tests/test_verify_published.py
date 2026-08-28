"""Tests voor de publieke verificatie, met nadruk op bewijsbare versheid.

Het risico dat hier wordt afgedekt: GitHub Pages is eventually consistent, dus
na een push kan het publieke eindpunt nog even de vórige versie serveren. Die
oude versie heeft een geldig schema en een geldig contract, en zou dus zonder
extra controle groen worden. De content hash maakt het verschil ondubbelzinnig.
"""

from __future__ import annotations

import copy

import pytest

from src.normalize_kistje import (
    compute_content_hash,
    compute_publication_hash,
)
from src.verify_published import (
    VerificationError,
    default_base_url,
    fetch_fresh_status,
    verify_week_document,
)

MODIFIED_NEW = "2026-08-27T12:20:54"
MODIFIED_OLD = "2026-08-20T11:05:12"


def _week_document(modified: str = MODIFIED_NEW, products=None) -> dict:
    document = {
        "schema_version": 1,
        "week": "2026-W35",
        "week_number": 35,
        "iso_year": 2026,
        "date_range_text": "24 t/m 30 aug",
        "source": {
            "page_id": 19172,
            "modified": modified,
            "fetched_at": "2026-08-28T14:15:16+02:00",
        },
        "vegetables": [{"name": products or "Snijbiet", "category": "vegetable"}],
        "fruit": [{"name": "Bananen", "category": "fruit"}],
        "planner_additions": [{"name": "Eieren", "quantity": 6}],
    }
    document["content_hash"] = compute_content_hash(document)
    return document


def _status(document: dict) -> dict:
    hashes = {document["week"]: document["content_hash"]}
    return {
        "status": "ok",
        "source_modified": document["source"]["modified"],
        "published_weeks": [document["week"]],
        "week_hashes": hashes,
        "publication_hash": compute_publication_hash(hashes),
    }


def _patch_fetch(monkeypatch, responses):
    calls = {"n": 0}

    def fake_fetch(url, backoffs=(), sleep=None):
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[index]

    monkeypatch.setattr("src.verify_published.fetch_json", fake_fetch)
    return calls


# --- Hash-eigenschappen ----------------------------------------------------


def test_hash_is_stabiel_over_runs():
    """Twee runs over dezelfde bron moeten dezelfde hash geven."""
    first = _week_document()
    second = _week_document()
    second["source"]["fetched_at"] = "2026-08-29T09:00:00+02:00"
    assert first["content_hash"] == second["content_hash"]


def test_hash_negeert_fetched_at_maar_niet_modified():
    baseline = _week_document()
    other_modified = _week_document(MODIFIED_OLD)
    assert baseline["content_hash"] != other_modified["content_hash"]


def test_hash_verandert_bij_andere_producten():
    baseline = _week_document()
    changed = _week_document(products="Spinazie")
    assert baseline["content_hash"] != changed["content_hash"]


def test_publication_hash_volgt_de_weekhashes():
    document = _week_document()
    assert _status(document)["publication_hash"] == compute_publication_hash(
        {document["week"]: document["content_hash"]}
    )


# --- Gevraagde versheidsscenario's -----------------------------------------


def test_publieke_payload_gelijk_aan_nieuwe_payload_slaagt(monkeypatch):
    document = _week_document()
    status = _status(document)
    _patch_fetch(monkeypatch, [status])

    result = fetch_fresh_status(
        "https://x", status["publication_hash"], backoffs=(), sleep=lambda _: None
    )
    assert result["publication_hash"] == status["publication_hash"]
    verify_week_document(document, "2026-W35", document["content_hash"])


def test_zelfde_schema_maar_oudere_modified_faalt(monkeypatch):
    """Contract klopt, inhoud is oud: moet worden geweigerd."""
    fresh = _week_document(MODIFIED_NEW)
    stale = _week_document(MODIFIED_OLD)
    _patch_fetch(monkeypatch, [_status(stale)])

    with pytest.raises(VerificationError, match="niet doorgekomen"):
        fetch_fresh_status(
            "https://x",
            _status(fresh)["publication_hash"],
            backoffs=(1,),
            sleep=lambda _: None,
        )


def test_andere_producten_falen():
    fresh = _week_document()
    different = _week_document(products="Spinazie")
    with pytest.raises(VerificationError, match="oudere versie"):
        verify_week_document(different, "2026-W35", fresh["content_hash"])


def test_tijdelijk_oude_pages_respons_gevolgd_door_verse(monkeypatch):
    """Eventual consistency: eerst oud, dan vers -> retry en slagen."""
    fresh = _week_document(MODIFIED_NEW)
    stale = _week_document(MODIFIED_OLD)
    slept: list[int] = []
    _patch_fetch(monkeypatch, [_status(stale), _status(stale), _status(fresh)])

    result = fetch_fresh_status(
        "https://x",
        _status(fresh)["publication_hash"],
        backoffs=(1, 2, 3),
        sleep=slept.append,
    )
    assert result["publication_hash"] == _status(fresh)["publication_hash"]
    assert slept == [1, 2]


def test_verwachte_versie_wordt_nooit_zichtbaar(monkeypatch):
    fresh = _week_document(MODIFIED_NEW)
    stale = _week_document(MODIFIED_OLD)
    slept: list[int] = []
    _patch_fetch(monkeypatch, [_status(stale)])

    with pytest.raises(VerificationError, match="niet doorgekomen"):
        fetch_fresh_status(
            "https://x",
            _status(fresh)["publication_hash"],
            backoffs=(1, 2),
            sleep=slept.append,
        )
    assert slept == [1, 2]


# --- Integriteit -----------------------------------------------------------


def test_gemanipuleerde_inhoud_met_oude_hash_wordt_betrapt():
    """De hash moet de inhoud dekken, niet alleen meegeleverd zijn."""
    document = _week_document()
    document["vegetables"][0]["name"] = "Iets anders"
    with pytest.raises(VerificationError, match="dekt de inhoud niet"):
        verify_week_document(document, "2026-W35")


def test_ontbrekende_content_hash_wordt_geweigerd():
    document = _week_document()
    del document["content_hash"]
    with pytest.raises(VerificationError, match="geen content_hash"):
        verify_week_document(document, "2026-W35")


# --- Zonder verwachting: alleen contract -----------------------------------


def test_zonder_verwachting_wordt_niet_op_versheid_gewacht(monkeypatch):
    stale = _week_document(MODIFIED_OLD)
    calls = _patch_fetch(monkeypatch, [_status(stale)])
    status = fetch_fresh_status("https://x", "", backoffs=(), sleep=lambda _: None)
    assert status["publication_hash"]
    assert calls["n"] == 1


def test_weekdocument_zonder_verwachting_blijft_werken():
    verify_week_document(_week_document(MODIFIED_OLD), "2026-W35")


# --- Overig contract -------------------------------------------------------


def test_verkeerde_weeksleutel_wordt_geweigerd():
    with pytest.raises(VerificationError, match="komt niet overeen"):
        verify_week_document(_week_document(), "2026-W36")


def test_verkeerd_page_id_wordt_geweigerd():
    document = copy.deepcopy(_week_document())
    document["source"]["page_id"] = 1
    document["content_hash"] = compute_content_hash(document)
    with pytest.raises(VerificationError, match="page_id"):
        verify_week_document(document, "2026-W35")


def test_ontbrekende_eieren_worden_geweigerd():
    document = _week_document()
    document["planner_additions"] = []
    document["content_hash"] = compute_content_hash(document)
    with pytest.raises(VerificationError, match="6 eieren"):
        verify_week_document(document, "2026-W35")


def test_lege_groente_wordt_geweigerd():
    document = _week_document()
    document["vegetables"] = []
    document["content_hash"] = compute_content_hash(document)
    with pytest.raises(VerificationError, match="geen groente"):
        verify_week_document(document, "2026-W35")


# --- Base URL --------------------------------------------------------------


def test_base_url_wordt_afgeleid_uit_github_repository(monkeypatch):
    monkeypatch.delenv("PAGES_BASE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "JortGroen/weekhap")
    assert default_base_url() == "https://jortgroen.github.io/weekhap"


def test_expliciete_base_url_wint(monkeypatch):
    monkeypatch.setenv("PAGES_BASE_URL", "https://example.com/pad/")
    assert default_base_url() == "https://example.com/pad"


def test_zonder_repository_faalt_de_url_bepaling(monkeypatch):
    monkeypatch.delenv("PAGES_BASE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "")
    with pytest.raises(VerificationError, match="PAGES_BASE_URL"):
        default_base_url()
