"""Normalisatie van geparste weeksecties naar publiceerbare JSON.

Twee dingen gebeuren hier die makkelijk stilletjes fout gaan:

1. Jaarinferentie. De bron noemt alleen "Week 35", nooit het jaar. Het jaar
   wordt afgeleid uit de datumrange plus `modified`, nooit uit
   `datetime.now().year` -- dat zou rond de jaarwisseling de verkeerde week
   opleveren zonder dat iemand het merkt.
2. Tijdzones. WordPress levert `modified` in lokale tijd (Europe/Amsterdam) en
   `modified_gmt` in UTC. Beide worden bewaard; afgeleide berekeningen gebruiken
   de UTC-variant zodat de zomertijdwissel geen uur verschuift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from src.parse_kistje import WeekSection
from src.planner_week import iso_week_key

SCHEMA_VERSION = 1
PAGE_ID = 19172
PAGE_SLUG = "deze-week-in-je-kistje"
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")

# Vaste plannerregel: er zitten altijd 6 eieren bij het kistje. Die staan niet
# in de bron en worden hier expliciet als plannerregel toegevoegd.
PLANNER_ADDITIONS = [
    {
        "name": "Eieren",
        "quantity": 6,
        "unit": "stuks",
        "origin": "planner_rule",
    }
]

DUTCH_MONTHS = {
    "jan": 1, "januari": 1,
    "feb": 2, "februari": 2,
    "mrt": 3, "maart": 3,
    "apr": 4, "april": 4,
    "mei": 5,
    "jun": 6, "juni": 6,
    "jul": 7, "juli": 7,
    "aug": 8, "augustus": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_ALTERNATION = "|".join(sorted(DUTCH_MONTHS, key=len, reverse=True))
_DAY_MONTH_RE = re.compile(
    r"(\d{1,2})\s*(" + _MONTH_ALTERNATION + r")?", re.IGNORECASE
)
_RANGE_SEPARATOR_RE = re.compile(r"t/m|tot en met|-|–", re.IGNORECASE)


class NormalizeError(ValueError):
    """De geparste data kon niet betrouwbaar naar een weeksleutel worden vertaald."""


@dataclass
class DateRange:
    start_day: int
    start_month: int


def parse_date_range(text: str) -> DateRange | None:
    """Haal de startdag en -maand uit teksten als '24 t/m 30 aug'.

    Staat er bij de startdatum geen maand, dan wordt die van de einddatum
    overgenomen. Ligt de startdag daarbij na de einddag, dan loopt de range over
    een maandgrens en hoort de start bij de voorgaande maand.
    """
    if not text or not text.strip():
        return None

    parts = _RANGE_SEPARATOR_RE.split(text, maxsplit=1)
    start_match = _DAY_MONTH_RE.search(parts[0])
    if not start_match:
        return None

    start_day = int(start_match.group(1))
    start_month_name = start_match.group(2)

    end_day: int | None = None
    end_month_name: str | None = None
    if len(parts) > 1:
        end_match = _DAY_MONTH_RE.search(parts[1])
        if end_match:
            end_day = int(end_match.group(1))
            end_month_name = end_match.group(2)

    if start_month_name:
        start_month = DUTCH_MONTHS[start_month_name.lower()]
    elif end_month_name:
        end_month = DUTCH_MONTHS[end_month_name.lower()]
        # "29 t/m 4 sept" loopt over een maandgrens heen.
        if end_day is not None and start_day > end_day:
            start_month = 12 if end_month == 1 else end_month - 1
        else:
            start_month = end_month
    else:
        return None

    if not 1 <= start_day <= 31:
        return None
    return DateRange(start_day=start_day, start_month=start_month)


def infer_iso_year(week_number: int, date_range_text: str, reference: date) -> int:
    """Bepaal het ISO-jaar van een weeknummer.

    De datumrange is de sterkste aanwijzing: voor het juiste jaar valt de maandag
    van die ISO-week exact op de genoemde startdatum. Levert dat niets op, dan
    wint het jaar waarvan de week het dichtst bij `reference` (`modified`) ligt.
    """
    candidates = [reference.isocalendar()[0] + delta for delta in (-1, 0, 1)]

    mondays: dict[int, date] = {}
    for candidate in candidates:
        try:
            mondays[candidate] = date.fromisocalendar(candidate, week_number, 1)
        except ValueError:
            # Week 53 bestaat niet in ieder ISO-jaar.
            continue

    if not mondays:
        raise NormalizeError(
            "Week %d bestaat niet in de ISO-jaren rond %s" % (week_number, reference)
        )

    parsed_range = parse_date_range(date_range_text)
    if parsed_range is not None:
        matches = [
            year
            for year, monday in mondays.items()
            if monday.day == parsed_range.start_day
            and monday.month == parsed_range.start_month
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return min(matches, key=lambda year: abs((mondays[year] - reference).days))

    return min(mondays, key=lambda year: abs((mondays[year] - reference).days))


def _source_block(payload: dict, fetched_at: datetime) -> dict:
    return {
        "page_id": payload.get("id"),
        "url": payload.get("link"),
        "api_url": "https://hoevebiesland.nl/wp-json/wp/v2/pages/%s" % payload.get("id"),
        # `modified` is lokale tijd (Europe/Amsterdam); `modified_gmt` is UTC.
        # Beide bewaren voorkomt een stille uurfout rond de zomertijdwissel.
        "modified": payload.get("modified"),
        "modified_gmt": payload.get("modified_gmt"),
        "fetched_at": fetched_at.isoformat(),
    }


def reference_date(payload: dict) -> date:
    """De datum waar de jaarinferentie zich op baseert: `modified` uit WordPress."""
    raw = payload.get("modified_gmt") or payload.get("modified")
    if not raw:
        raise NormalizeError("Response bevat geen bruikbare 'modified'")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError as exc:
        raise NormalizeError("Kan 'modified' niet parsen: " + repr(raw)) from exc


def build_week_documents(
    payload: dict,
    sections: list[WeekSection],
    fetched_at: datetime | None = None,
) -> dict[str, dict]:
    """Bouw per week een publiceerbaar document, gesleuteld op '2026-W35'."""
    fetched_at = fetched_at or datetime.now(LOCAL_TZ)
    reference = reference_date(payload)
    source = _source_block(payload, fetched_at)

    documents: dict[str, dict] = {}
    for section in sections:
        iso_year = infer_iso_year(section.week_number, section.date_range_text, reference)
        key = iso_week_key(date.fromisocalendar(iso_year, section.week_number, 1))
        if key in documents:
            raise NormalizeError("Dubbele weeksleutel na jaarinferentie: " + key)
        documents[key] = {
            "schema_version": SCHEMA_VERSION,
            "week": key,
            "week_number": section.week_number,
            "iso_year": iso_year,
            "source": source,
            "date_range_text": section.date_range_text,
            "vegetables": [product.as_dict() for product in section.vegetables],
            "fruit": [product.as_dict() for product in section.fruit],
            "planner_additions": [dict(addition) for addition in PLANNER_ADDITIONS],
        }
    return documents


def build_latest(payload: dict, documents: dict[str, dict], fetched_at: datetime) -> dict:
    """De volledige meest recent opgehaalde bron, met alle gepubliceerde weken."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source": _source_block(payload, fetched_at),
        "published_weeks": sorted(documents),
        "weeks": {key: documents[key] for key in sorted(documents)},
    }


def build_status(
    payload: dict,
    documents: dict[str, dict],
    fetched_at: datetime,
    warnings: list[str],
    previous_status: dict | None = None,
) -> dict:
    """Statusbestand met verse-heid en een korte historie van `modified`.

    De historie maakt het publicatieritme achteraf meetbaar, zodat de
    run-frequentie later op waarnemingen kan worden gebaseerd in plaats van op
    een aanname.
    """
    modified = payload.get("modified_gmt") or payload.get("modified")
    history: list[str] = []
    if previous_status:
        history = [
            entry
            for entry in previous_status.get("observed_modified_history", [])
            if isinstance(entry, str)
        ]
    if modified and modified not in history:
        history.append(modified)
    history = sorted(set(history))[-20:]

    return {
        "status": "ok",
        "last_success": fetched_at.isoformat(),
        "source_modified": payload.get("modified"),
        "source_modified_gmt": payload.get("modified_gmt"),
        "published_weeks": sorted(documents),
        "warnings": warnings,
        "observed_modified_history": history,
    }


def validate_payload(payload: dict) -> None:
    """Harde eisen uit paragraaf 11 van het projectplan, op responseniveau."""
    if payload.get("id") != PAGE_ID:
        raise NormalizeError(
            "Onverwacht page id: %r (verwacht %d)" % (payload.get("id"), PAGE_ID)
        )
    if payload.get("slug") != PAGE_SLUG:
        raise NormalizeError(
            "Onverwachte slug: %r (verwacht %r)" % (payload.get("slug"), PAGE_SLUG)
        )
    raw_modified = payload.get("modified")
    if not raw_modified:
        raise NormalizeError("Response bevat geen 'modified'")
    try:
        datetime.fromisoformat(raw_modified)
    except ValueError as exc:
        raise NormalizeError("'modified' is niet parseerbaar: " + repr(raw_modified)) from exc

    content = (payload.get("content") or {}).get("rendered")
    if not content or not content.strip():
        raise NormalizeError("content.rendered is leeg")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
