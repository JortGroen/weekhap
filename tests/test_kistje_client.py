"""Unit tests voor de consumerclient: weekselectie, URL-bouw en foutgedrag.

Netwerk wordt hier volledig vervangen door stubs; de echte HTTP-gang zit in
tests/test_live_e2e.py.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date

import pytest

from src.kistje_client import (
    DEFAULT_BASE_URL,
    BoxContents,
    KistjeContractError,
    KistjeUnavailable,
    build_url,
    fetch_week,
    get_box_for_plan_start,
    week_key_for_plan_start,
)


def _document(week: str = "2026-W35", **overrides) -> dict:
    document = {
        "schema_version": 1,
        "week": week,
        "week_number": int(week.split("W")[1]),
        "date_range_text": "24 t/m 30 aug",
        "source": {"page_id": 19172, "modified": "2026-08-27T12:20:54"},
        "vegetables": [{"name": "Snijbiet", "category": "vegetable"}],
        "fruit": [{"name": "Bananen", "category": "fruit"}],
        "planner_additions": [
            {"name": "Eieren", "quantity": 6, "unit": "stuks", "origin": "planner_rule"}
        ],
    }
    document.update(overrides)
    return document


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, handler):
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return handler(request.full_url, len(calls))

    monkeypatch.setattr("src.kistje_client.urllib.request.urlopen", fake_urlopen)
    return calls


# --- Verplichte weekselectiecases ------------------------------------------


@pytest.mark.parametrize(
    "plan_start, expected_key",
    [
        (date(2026, 8, 27), "2026-W35"),  # donderdag zelf
        (date(2026, 8, 31), "2026-W35"),  # maandag -> donderdag ervoor
        (date(2026, 9, 3), "2026-W36"),
    ],
)
def test_verplichte_weekselectie(plan_start, expected_key):
    assert week_key_for_plan_start(plan_start) == expected_key


def test_weeknummer_is_altijd_tweecijferig():
    assert week_key_for_plan_start(date(2026, 1, 5)) == "2026-W01"


# --- URL-bouw en toegangsbeperking -----------------------------------------


def test_default_url():
    assert build_url("2026-W35") == (
        "https://jortgroen.github.io/weekhap/api/by-week/2026-W35.json"
    )


def test_base_url_zonder_slash_werkt_ook():
    base = DEFAULT_BASE_URL.rstrip("/")
    assert build_url("2026-W35", base) == build_url("2026-W35")


def test_http_wordt_geweigerd():
    with pytest.raises(KistjeContractError, match="https"):
        build_url("2026-W35", "http://jortgroen.github.io/weekhap/api/by-week/")


def test_andere_host_wordt_geweigerd():
    with pytest.raises(KistjeContractError, match="toegestane lijst"):
        build_url("2026-W35", "https://evil.example.com/weekhap/api/by-week/")


def test_pad_buiten_de_api_wordt_geweigerd():
    with pytest.raises(KistjeContractError, match="buiten"):
        build_url("2026-W35", "https://jortgroen.github.io/anders/")


# --- Geslaagd ophalen ------------------------------------------------------


def test_fetch_week_levert_kistinhoud(monkeypatch):
    _patch_urlopen(monkeypatch, lambda url, n: _FakeResponse(_document()))
    box = fetch_week("2026-W35")

    assert isinstance(box, BoxContents)
    assert box.week == "2026-W35"
    assert box.product_names == ["Snijbiet", "Bananen"]
    assert box.url.startswith("https://jortgroen.github.io/")


def test_volledige_flow_vanaf_planstart(monkeypatch):
    calls = _patch_urlopen(monkeypatch, lambda url, n: _FakeResponse(_document()))
    box = get_box_for_plan_start(date(2026, 8, 31))

    assert calls == [
        "https://jortgroen.github.io/weekhap/api/by-week/2026-W35.json"
    ]
    assert box.week == "2026-W35"


def test_zes_eieren_zitten_in_de_plannerregels(monkeypatch):
    _patch_urlopen(monkeypatch, lambda url, n: _FakeResponse(_document()))
    box = fetch_week("2026-W35")

    eggs = [item for item in box.planner_additions if item["name"] == "Eieren"]
    assert len(eggs) == 1
    assert eggs[0]["quantity"] == 6


def test_eieren_worden_toegevoegd_als_de_bron_ze_mist(monkeypatch):
    _patch_urlopen(
        monkeypatch,
        lambda url, n: _FakeResponse(_document(planner_additions=[])),
    )
    box = fetch_week("2026-W35")

    eggs = [item for item in box.planner_additions if item["name"] == "Eieren"]
    assert len(eggs) == 1
    assert eggs[0]["quantity"] == 6


def test_eieren_worden_niet_gedupliceerd(monkeypatch):
    _patch_urlopen(monkeypatch, lambda url, n: _FakeResponse(_document()))
    box = fetch_week("2026-W35")
    assert len(box.planner_additions) == 1


def test_shopping_items_bevat_kist_en_plannerregels(monkeypatch):
    _patch_urlopen(monkeypatch, lambda url, n: _FakeResponse(_document()))
    items = fetch_week("2026-W35").shopping_items()

    assert [item["name"] for item in items] == ["Snijbiet", "Bananen", "Eieren"]
    assert items[-1]["source"] == "planner_rule"


# --- Foutgedrag: nooit stilzwijgend een andere week ------------------------


def test_verkeerde_week_in_json_wordt_geweigerd(monkeypatch):
    """De kern van de veiligheid: een oude week mag nooit doorgaan voor een nieuwe."""
    _patch_urlopen(
        monkeypatch, lambda url, n: _FakeResponse(_document(week="2026-W34"))
    )
    with pytest.raises(KistjeContractError, match="komt niet overeen"):
        fetch_week("2026-W35")


def test_onbekende_schema_version_wordt_geweigerd(monkeypatch):
    _patch_urlopen(
        monkeypatch, lambda url, n: _FakeResponse(_document(schema_version=2))
    )
    with pytest.raises(KistjeContractError, match="schema_version"):
        fetch_week("2026-W35")


def test_ontbrekende_freshness_wordt_geweigerd(monkeypatch):
    _patch_urlopen(monkeypatch, lambda url, n: _FakeResponse(_document(source={})))
    with pytest.raises(KistjeContractError, match="modified"):
        fetch_week("2026-W35")


def test_lege_groente_wordt_geweigerd(monkeypatch):
    _patch_urlopen(monkeypatch, lambda url, n: _FakeResponse(_document(vegetables=[])))
    with pytest.raises(KistjeContractError, match="vegetables"):
        fetch_week("2026-W35")


def test_product_zonder_naam_wordt_geweigerd(monkeypatch):
    _patch_urlopen(
        monkeypatch,
        lambda url, n: _FakeResponse(_document(fruit=[{"category": "fruit"}])),
    )
    with pytest.raises(KistjeContractError, match="zonder naam"):
        fetch_week("2026-W35")


def test_verkeerd_aantal_eieren_wordt_geweigerd(monkeypatch):
    _patch_urlopen(
        monkeypatch,
        lambda url, n: _FakeResponse(
            _document(planner_additions=[{"name": "Eieren", "quantity": 4}])
        ),
    )
    with pytest.raises(KistjeContractError, match="6 eieren"):
        fetch_week("2026-W35")


def test_ongeldige_json_geeft_expliciete_fout(monkeypatch):
    class BadResponse(_FakeResponse):
        def read(self):
            return b"<html>oeps</html>"

    monkeypatch.setattr(
        "src.kistje_client.urllib.request.urlopen",
        lambda request, timeout=None: BadResponse({}),
    )
    with pytest.raises(KistjeUnavailable, match="Ongeldige JSON"):
        fetch_week("2026-W35", backoffs=(), sleep=lambda _: None)


# --- Retry-gedrag ----------------------------------------------------------


def test_404_wordt_niet_opnieuw_geprobeerd(monkeypatch):
    """Een ontbrekende week is definitief; herhalen verandert daar niets aan."""

    def handler(url, attempt):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    calls = _patch_urlopen(monkeypatch, handler)
    with pytest.raises(KistjeUnavailable, match="404"):
        fetch_week("2026-W99", sleep=lambda _: None)

    assert len(calls) == 1


def test_tijdelijke_fout_wordt_opnieuw_geprobeerd(monkeypatch):
    def handler(url, attempt):
        if attempt == 1:
            raise TimeoutError("te traag")
        return _FakeResponse(_document())

    calls = _patch_urlopen(monkeypatch, handler)
    box = fetch_week("2026-W35", sleep=lambda _: None)

    assert len(calls) == 2
    assert box.week == "2026-W35"


def test_retry_stopt_na_het_maximum(monkeypatch):
    def handler(url, attempt):
        raise TimeoutError("blijft traag")

    calls = _patch_urlopen(monkeypatch, handler)
    with pytest.raises(KistjeUnavailable, match="mislukt na 3 pogingen"):
        fetch_week("2026-W35", sleep=lambda _: None)

    assert len(calls) == 3


def test_server_fout_wordt_opnieuw_geprobeerd(monkeypatch):
    def handler(url, attempt):
        if attempt < 3:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
        return _FakeResponse(_document())

    calls = _patch_urlopen(monkeypatch, handler)
    box = fetch_week("2026-W35", sleep=lambda _: None)

    assert len(calls) == 3
    assert box.week == "2026-W35"
