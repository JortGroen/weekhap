"""Tests voor docs/openapi.json.

Twee dingen worden bewaakt:

1. Importeerbaarheid. Tools die dit schema inlezen hanteren limieten -- onder
   meer 300 tekens voor een operation description. Een te lange tekst wordt pas
   bij het importeren zichtbaar, dus die grens wordt hier vastgelegd.
2. Accuraatheid. Documentatie die stilletjes achterloopt op de gepubliceerde
   JSON is erger dan geen documentatie, dus het schema wordt vergeleken met wat
   de pijplijn werkelijk genereert.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.normalize_kistje import build_latest, build_status, build_week_documents
from src.parse_kistje import parse_content

SPEC_PATH = Path(__file__).parent.parent / "docs" / "openapi.json"
FIXTURE = Path(__file__).parent / "fixtures" / "wordpress_response.json"

# Grens die importerende tools hanteren voor operation descriptions.
MAX_DESCRIPTION_LENGTH = 300


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def generated() -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sections = parse_content(payload["content"]["rendered"])
    return build_week_documents(payload, sections)


def _operations(spec: dict):
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            yield path, method, operation


# --- Importeerbaarheid -----------------------------------------------------


def test_operation_descriptions_blijven_binnen_de_limiet(spec):
    """Te lange descriptions worden bij het importeren geweigerd."""
    overlong = [
        "%s %s (%s): %d tekens"
        % (method, path, operation["operationId"], len(operation.get("description", "")))
        for path, method, operation in _operations(spec)
        if len(operation.get("description", "")) > MAX_DESCRIPTION_LENGTH
    ]
    assert not overlong, "description(s) boven %d tekens: %s" % (
        MAX_DESCRIPTION_LENGTH,
        "; ".join(overlong),
    )


def test_info_description_blijft_binnen_de_limiet(spec):
    assert len(spec["info"]["description"]) <= MAX_DESCRIPTION_LENGTH


def test_iedere_operatie_heeft_id_summary_en_description(spec):
    for path, method, operation in _operations(spec):
        assert operation.get("operationId"), "%s %s mist operationId" % (method, path)
        assert operation.get("summary"), "%s %s mist summary" % (method, path)
        assert operation.get("description"), "%s %s mist description" % (method, path)


def test_operation_ids_zijn_uniek(spec):
    ids = [operation["operationId"] for _, _, operation in _operations(spec)]
    assert len(ids) == len(set(ids)), "dubbele operationId: %s" % ids


def test_alle_refs_zijn_oplosbaar(spec):
    schemas = spec["components"]["schemas"]

    def check(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref:
                assert ref.startswith("#/components/schemas/"), ref
                assert ref.split("/")[-1] in schemas, "onbekende ref: %s" % ref
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(spec)


def test_server_wijst_naar_de_publieke_pages_url(spec):
    urls = [server["url"] for server in spec["servers"]]
    assert urls == ["https://jortgroen.github.io/weekhap"]


# --- Accuraatheid tegenover de werkelijke output ---------------------------


def test_alle_gepubliceerde_paden_zijn_gedocumenteerd(spec):
    assert set(spec["paths"]) == {
        "/api/by-week/{week}.json",
        "/api/status.json",
        "/api/latest.json",
    }


def test_weekbox_documenteert_alle_velden_die_worden_gepubliceerd(spec, generated):
    documented = set(spec["components"]["schemas"]["WeekBox"]["properties"])
    actual = set(next(iter(generated.values())))
    missing = actual - documented
    assert not missing, "niet gedocumenteerde velden in weekbestand: %s" % sorted(missing)


def test_product_documenteert_alle_velden_die_worden_gepubliceerd(spec, generated):
    documented = set(spec["components"]["schemas"]["Product"]["properties"])
    document = next(iter(generated.values()))
    actual = set(document["vegetables"][0])
    missing = actual - documented
    assert not missing, "niet gedocumenteerde productvelden: %s" % sorted(missing)


def test_status_documenteert_alle_velden_die_worden_gepubliceerd(spec, generated):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    from datetime import datetime

    status = build_status(payload, generated, datetime.now().astimezone(), [])
    documented = set(spec["components"]["schemas"]["Status"]["properties"])
    missing = set(status) - documented
    assert not missing, "niet gedocumenteerde statusvelden: %s" % sorted(missing)


def test_latest_documenteert_alle_velden_die_worden_gepubliceerd(spec, generated):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    from datetime import datetime

    latest = build_latest(payload, generated, datetime.now().astimezone())
    documented = set(spec["components"]["schemas"]["Latest"]["properties"])
    missing = set(latest) - documented
    assert not missing, "niet gedocumenteerde latest-velden: %s" % sorted(missing)


def test_content_hash_is_verplicht_in_het_schema(spec):
    required = spec["components"]["schemas"]["WeekBox"]["required"]
    assert "content_hash" in required
