"""Pijplijn: ophalen -> valideren -> parsen -> normaliseren -> publiceren.

Last-known-good staat voorop. Faalt een stap, dan wordt er niets in docs/
aangeraakt en eindigt het proces met een exitcode != 0, zodat de workflow
zichtbaar rood wordt en de bestaande publicatie ongemoeid blijft.

Bestanden worden alleen herschreven als de inhoud werkelijk verandert. Zonder
die controle zou `fetched_at` iedere run een nieuwe commit veroorzaken -- vijf
per dag, terwijl de bron ongeveer eens per week wijzigt.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from src.fetch_kistje import API_URL, FetchError, fetch_with_fallback
from src.normalize_kistje import (
    LOCAL_TZ,
    NormalizeError,
    build_latest,
    build_status,
    build_week_documents,
    validate_payload,
)
from src.parse_kistje import ParseError, parse_content, quality_warnings

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_API = REPO_ROOT / "docs" / "api"

# Velden die iedere run veranderen zonder dat de inhoud anders is. Ze worden
# genegeerd bij de wijzigingsvergelijking, maar wel gepubliceerd.
VOLATILE_KEYS = {"fetched_at", "last_success"}


def _strip_volatile(value):
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _dump(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_if_changed(path: Path, document: dict) -> bool:
    """Schrijf alleen als de niet-vluchtige inhoud afwijkt. Geeft True bij schrijven."""
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            existing = None
        if existing is not None and _strip_volatile(existing) == _strip_volatile(document):
            return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(document), encoding="utf-8")
    return True


def _display_path(path: Path) -> str:
    """Pad relatief aan de repo tonen, maar niet struikelen over paden erbuiten."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def run(payload: dict, docs_api: Path = DOCS_API) -> dict:
    """Valideer, parse en publiceer een opgehaalde response."""
    validate_payload(payload)

    sections = parse_content(payload["content"]["rendered"])
    warnings = quality_warnings(sections)
    for warning in warnings:
        print("WAARSCHUWING: " + warning, file=sys.stderr)

    fetched_at = datetime.now(LOCAL_TZ)
    documents = build_week_documents(payload, sections, fetched_at=fetched_at)

    written: list[str] = []
    for key in sorted(documents):
        target = docs_api / "by-week" / (key + ".json")
        if write_if_changed(target, documents[key]):
            written.append(_display_path(target))

    latest_path = docs_api / "latest.json"
    if write_if_changed(latest_path, build_latest(payload, documents, fetched_at)):
        written.append(_display_path(latest_path))

    status_path = docs_api / "status.json"
    status = build_status(
        payload,
        documents,
        fetched_at,
        warnings,
        previous_status=_read_json(status_path),
    )
    if write_if_changed(status_path, status):
        written.append(_display_path(status_path))

    return {
        "published_weeks": sorted(documents),
        "written": written,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bouw de kistje-JSON-publicatie")
    parser.add_argument(
        "--from-file",
        help="Gebruik een lokale response in plaats van een live fetch",
    )
    parser.add_argument(
        "--raw-out",
        help="Bewaar de ruwe response (handig als workflow artifact bij debugging)",
    )
    args = parser.parse_args(argv)

    try:
        if args.from_file:
            payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        else:
            print("Ophalen: " + API_URL)
            payload = fetch_with_fallback()

        if args.raw_out:
            Path(args.raw_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        result = run(payload)
    except (FetchError, ParseError, NormalizeError) as exc:
        print("MISLUKT: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        print(
            "Bestaande publicatie in docs/ is ongewijzigd gelaten "
            "(last-known-good).",
            file=sys.stderr,
        )
        return 1

    print("Gepubliceerde weken: " + ", ".join(result["published_weeks"]))
    if result["written"]:
        print("Gewijzigde bestanden:")
        for path in result["written"]:
            print("  " + path)
    else:
        print("Geen inhoudelijke wijzigingen; niets herschreven.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
