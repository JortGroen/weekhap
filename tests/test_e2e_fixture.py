"""End-to-end test van de volledige pijplijn op de fixture, zonder netwerk.

Dit is de deterministische tegenhanger van de live E2E-test in de workflow: hij
draait dezelfde code, maar tegen een opgeslagen response en een tijdelijke
docs/-map.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.build import run, write_if_changed
from src.normalize_kistje import PLANNER_ADDITIONS
from src.planner_week import week_key_for_plan_start

FIXTURE = Path(__file__).parent / "fixtures" / "wordpress_response.json"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def published(tmp_path, payload):
    result = run(payload, docs_api=tmp_path)
    return tmp_path, result


def test_weekbestanden_worden_aangemaakt(published):
    docs_api, result = published
    assert result["published_weeks"] == ["2026-W35", "2026-W36"]
    assert (docs_api / "by-week" / "2026-W35.json").exists()
    assert (docs_api / "by-week" / "2026-W36.json").exists()
    assert (docs_api / "latest.json").exists()
    assert (docs_api / "status.json").exists()


def test_weekbestand_heeft_verwacht_contract(published):
    docs_api, _ = published
    document = json.loads(
        (docs_api / "by-week" / "2026-W35.json").read_text(encoding="utf-8")
    )

    assert document["schema_version"] == 1
    assert document["week"] == "2026-W35"
    assert document["week_number"] == 35
    assert document["date_range_text"] == "24 t/m 30 aug"
    assert document["source"]["page_id"] == 19172
    assert document["source"]["modified"] == "2026-08-27T12:20:54"
    assert document["source"]["modified_gmt"] == "2026-08-27T10:20:54"
    assert document["source"]["fetched_at"]
    assert len(document["vegetables"]) == 4
    assert len(document["fruit"]) == 4


def test_planner_additions_bevat_zes_eieren(published):
    docs_api, _ = published
    for key in ("2026-W35", "2026-W36"):
        document = json.loads(
            (docs_api / "by-week" / (key + ".json")).read_text(encoding="utf-8")
        )
        additions = document["planner_additions"]
        eggs = [item for item in additions if item["name"] == "Eieren"]
        assert len(eggs) == 1
        assert eggs[0]["quantity"] == 6
        assert eggs[0]["unit"] == "stuks"
        assert eggs[0]["origin"] == "planner_rule"


def test_planner_additions_worden_niet_gedeeld_tussen_weken(published):
    """Iedere week krijgt een eigen kopie, zodat mutatie niet doorlekt."""
    docs_api, _ = published
    document = json.loads(
        (docs_api / "by-week" / "2026-W35.json").read_text(encoding="utf-8")
    )
    assert document["planner_additions"] is not PLANNER_ADDITIONS


def test_latest_bevat_alle_gepubliceerde_weken(published):
    docs_api, _ = published
    latest = json.loads((docs_api / "latest.json").read_text(encoding="utf-8"))
    assert latest["published_weeks"] == ["2026-W35", "2026-W36"]
    assert set(latest["weeks"]) == {"2026-W35", "2026-W36"}
    assert latest["source"]["page_id"] == 19172


def test_status_bevat_bron_en_weken(published):
    docs_api, _ = published
    status = json.loads((docs_api / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "ok"
    assert status["published_weeks"] == ["2026-W35", "2026-W36"]
    assert status["source_modified"] == "2026-08-27T12:20:54"
    assert status["warnings"] == []
    assert status["observed_modified_history"] == ["2026-08-27T10:20:54"]


def test_plannerketen_vindt_de_juiste_week(published):
    """De keten uit paragraaf 17: planstart -> donderdag -> weeksleutel -> bestand."""
    docs_api, _ = published

    key = week_key_for_plan_start(date(2026, 8, 31))  # maandag
    assert key == "2026-W35"

    target = docs_api / "by-week" / (key + ".json")
    assert target.exists()

    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["week"] == key
    assert document["schema_version"] == 1

    box = document["vegetables"] + document["fruit"]
    assert len(box) == 8
    # Het 2-persoonskistje bevat ook de *-producten.
    assert all(item["included_in_two_person_box"] for item in box)
    assert any(item["excluded_from_one_person_box"] for item in box)


def test_tweede_run_schrijft_niets_opnieuw(tmp_path, payload):
    """Zonder inhoudelijke wijziging mag er geen nieuwe commit ontstaan."""
    first = run(payload, docs_api=tmp_path)
    assert first["written"], "eerste run hoort bestanden te schrijven"

    second = run(payload, docs_api=tmp_path)
    assert second["written"] == []


def test_gewijzigde_bron_leidt_wel_tot_schrijven(tmp_path, payload):
    run(payload, docs_api=tmp_path)

    changed = json.loads(json.dumps(payload))
    changed["content"]["rendered"] = changed["content"]["rendered"].replace(
        "Snijbiet", "Spinazie"
    )
    result = run(changed, docs_api=tmp_path)

    assert any("2026-W35" in path for path in result["written"])


def test_write_if_changed_negeert_alleen_vluchtige_velden(tmp_path):
    target = tmp_path / "doc.json"

    assert write_if_changed(target, {"a": 1, "fetched_at": "t1"}) is True
    assert write_if_changed(target, {"a": 1, "fetched_at": "t2"}) is False
    assert write_if_changed(target, {"a": 2, "fetched_at": "t2"}) is True


def test_mislukte_run_laat_bestaande_publicatie_staan(tmp_path, payload):
    """Last-known-good: een kapotte response mag niets overschrijven."""
    run(payload, docs_api=tmp_path)
    before = (tmp_path / "by-week" / "2026-W35.json").read_text(encoding="utf-8")

    broken = json.loads(json.dumps(payload))
    broken["content"]["rendered"] = "<p>Onderhoud</p>"

    with pytest.raises(Exception):
        run(broken, docs_api=tmp_path)

    after = (tmp_path / "by-week" / "2026-W35.json").read_text(encoding="utf-8")
    assert after == before


# --- Tijdstempels in UTC ---------------------------------------------------


def test_tijdstempels_zijn_utc_met_z_suffix(published):
    """Machineleesbare tijdstempels staan in UTC, zonder tijdzone-interpretatie."""
    import re

    docs_api, _ = published
    utc_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    document = json.loads(
        (docs_api / "by-week" / "2026-W35.json").read_text(encoding="utf-8")
    )
    assert utc_pattern.match(document["source"]["fetched_at"])

    status = json.loads((docs_api / "status.json").read_text(encoding="utf-8"))
    assert utc_pattern.match(status["last_success"])

    # De bronvelden komen zo van WordPress en blijven ongemoeid.
    assert document["source"]["modified"] == "2026-08-27T12:20:54"
    assert document["source"]["modified_gmt"] == "2026-08-27T10:20:54"


def test_content_hash_negeert_fetched_at(payload):
    """Een ander ophaalmoment mag de hash niet veranderen."""
    from datetime import datetime, timezone

    from src.normalize_kistje import build_week_documents
    from src.parse_kistje import parse_content

    sections = parse_content(payload["content"]["rendered"])
    first = build_week_documents(
        payload, sections, fetched_at=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    )
    second = build_week_documents(
        payload, sections, fetched_at=datetime(2026, 9, 1, 6, 30, tzinfo=timezone.utc)
    )

    assert first["2026-W35"]["source"]["fetched_at"] != second["2026-W35"]["source"]["fetched_at"]
    assert first["2026-W35"]["content_hash"] == second["2026-W35"]["content_hash"]


# --- Geforceerd herschrijven ----------------------------------------------


def test_force_herschrijft_ook_zonder_inhoudelijke_wijziging(tmp_path, payload):
    """Nodig bij een schemawijziging die alleen vluchtige velden raakt."""
    run(payload, docs_api=tmp_path)
    assert run(payload, docs_api=tmp_path)["written"] == []

    forced = run(payload, docs_api=tmp_path, force=True)
    assert len(forced["written"]) == 4  # twee weken, latest en status
