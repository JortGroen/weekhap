"""Tests voor de publieke verificatie, met nadruk op de versheidscontrole.

Zonder die controle kan verify-published de vorige Pages-versie valideren en
tóch groen worden: Pages herbouwt asynchroon na de push. Deze tests leggen vast
dat dat niet meer kan.
"""

from __future__ import annotations

import pytest

from src.verify_published import (
    VerificationError,
    default_base_url,
    fetch_fresh_status,
    verify_week_document,
)

MODIFIED_NEW = "2026-08-27T12:20:54"
MODIFIED_OLD = "2026-08-20T11:05:12"


def _status(modified: str) -> dict:
    return {
        "status": "ok",
        "source_modified": modified,
        "published_weeks": ["2026-W35"],
    }


def _week_document(modified: str = MODIFIED_NEW) -> dict:
    return {
        "schema_version": 1,
        "week": "2026-W35",
        "source": {
            "page_id": 19172,
            "modified": modified,
            "fetched_at": "2026-08-28T14:15:16+02:00",
        },
        "vegetables": [{"name": "Snijbiet"}],
        "fruit": [{"name": "Bananen"}],
        "planner_additions": [{"name": "Eieren", "quantity": 6}],
    }


def _patch_fetch(monkeypatch, responses):
    """Laat fetch_json achtereenvolgens de opgegeven payloads teruggeven."""
    calls = {"n": 0}

    def fake_fetch(url, backoffs=(), sleep=None):
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[index]

    monkeypatch.setattr("src.verify_published.fetch_json", fake_fetch)
    return calls


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


# --- Versheidscontrole -----------------------------------------------------


def test_verse_status_wordt_direct_geaccepteerd(monkeypatch):
    calls = _patch_fetch(monkeypatch, [_status(MODIFIED_NEW)])
    status = fetch_fresh_status("https://x", MODIFIED_NEW, backoffs=(), sleep=lambda _: None)
    assert status["source_modified"] == MODIFIED_NEW
    assert calls["n"] == 1


def test_zonder_verwachting_wordt_niet_op_versheid_gewacht(monkeypatch):
    """Handmatige run buiten de workflow: alleen ophalen, niet vergelijken."""
    calls = _patch_fetch(monkeypatch, [_status(MODIFIED_OLD)])
    status = fetch_fresh_status("https://x", "", backoffs=(), sleep=lambda _: None)
    assert status["source_modified"] == MODIFIED_OLD
    assert calls["n"] == 1


def test_er_wordt_gewacht_tot_pages_is_bijgewerkt(monkeypatch):
    """Pages serveert eerst de oude versie en daarna de nieuwe."""
    slept: list[int] = []
    _patch_fetch(
        monkeypatch,
        [_status(MODIFIED_OLD), _status(MODIFIED_OLD), _status(MODIFIED_NEW)],
    )
    status = fetch_fresh_status(
        "https://x", MODIFIED_NEW, backoffs=(1, 2, 3), sleep=slept.append
    )
    assert status["source_modified"] == MODIFIED_NEW
    assert slept == [1, 2]


def test_verouderde_pages_versie_faalt_expliciet(monkeypatch):
    """De kern: een oude publicatie mag nooit stilzwijgend groen worden."""
    _patch_fetch(monkeypatch, [_status(MODIFIED_OLD)])
    with pytest.raises(VerificationError, match="niet doorgekomen"):
        fetch_fresh_status(
            "https://x", MODIFIED_NEW, backoffs=(1,), sleep=lambda _: None
        )


# --- Weekdocument ----------------------------------------------------------


def test_weekdocument_met_juiste_versheid_wordt_geaccepteerd():
    verify_week_document(_week_document(), "2026-W35", MODIFIED_NEW)


def test_weekdocument_uit_oudere_publicatie_wordt_geweigerd():
    with pytest.raises(VerificationError, match="serveert source.modified"):
        verify_week_document(_week_document(MODIFIED_OLD), "2026-W35", MODIFIED_NEW)


def test_weekdocument_zonder_verwachting_blijft_werken():
    verify_week_document(_week_document(MODIFIED_OLD), "2026-W35")


def test_verkeerde_weeksleutel_wordt_geweigerd():
    with pytest.raises(VerificationError, match="komt niet overeen"):
        verify_week_document(_week_document(), "2026-W36", MODIFIED_NEW)


def test_verkeerd_page_id_wordt_geweigerd():
    document = _week_document()
    document["source"]["page_id"] = 1
    with pytest.raises(VerificationError, match="page_id"):
        verify_week_document(document, "2026-W35", MODIFIED_NEW)


def test_ontbrekende_eieren_worden_geweigerd():
    document = _week_document()
    document["planner_additions"] = []
    with pytest.raises(VerificationError, match="6 eieren"):
        verify_week_document(document, "2026-W35", MODIFIED_NEW)


def test_lege_groente_wordt_geweigerd():
    document = _week_document()
    document["vegetables"] = []
    with pytest.raises(VerificationError, match="geen groente"):
        verify_week_document(document, "2026-W35", MODIFIED_NEW)
