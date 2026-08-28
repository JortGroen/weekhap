"""Parser voor de Hoeve Biesland 'Deze week in je kistje'-pagina.

De bron is WordPress-HTML doorspekt met Visual Composer-shortcodes. De opzet
volgt bewust een klein state machine over de DOM in plaats van een grote regex:
weeksecties worden herkend aan de <h3>-koppen, en iedere <ul> wordt gekoppeld
aan de laatst gepasseerde week en het dichtstbijzijnde categorie-label.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

WEEK_HEADING_RE = re.compile(r"^\s*week\s+(\d{1,2})\s*$", re.IGNORECASE)
CERTIFICATION_RE = re.compile(r"\((biologisch|biodynamisch)\)", re.IGNORECASE)
# De bron zet de markering los achter de regel, soms met dubbele spatie.
EXCLUSION_MARKER_RE = re.compile(r"\s*\*\s*$")
# Het schemavoorbeeld toont origin zonder voorzetsel ("eigen tuin", niet
# "uit eigen tuin"), dus wordt een leidend "van"/"uit" weggehaald.
LEADING_CONNECTOR_RE = re.compile(r"^(?:van|uit)\s+", re.IGNORECASE)

VEGETABLE_LABEL_RE = re.compile(r"groente", re.IGNORECASE)
FRUIT_LABEL_RE = re.compile(r"fruit", re.IGNORECASE)


class ParseError(ValueError):
    """De broninhoud kon niet betrouwbaar worden geinterpreteerd."""


@dataclass
class Product:
    name: str
    category: str  # "vegetable" | "fruit"
    origin: str
    certification: str | None
    excluded_from_one_person_box: bool
    source_line: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "origin": self.origin,
            "certification": self.certification,
            "excluded_from_one_person_box": self.excluded_from_one_person_box,
            # Het huishouden gebruikt een 2-persoonskistje: daar zit alles in,
            # ook de producten met een *-markering.
            "included_in_two_person_box": True,
            "source_line": self.source_line,
        }


@dataclass
class WeekSection:
    week_number: int
    date_range_text: str
    # Alleen voor foutmeldingen: maakt een gewijzigd bronformaat
    # diagnosticeerbaar vanuit het GitHub Actions-log.
    heading_text: str = ""
    source_excerpt: str = ""
    vegetables: list[Product] = field(default_factory=list)
    fruit: list[Product] = field(default_factory=list)


def _normalize_whitespace(text: str) -> str:
    # Non-breaking spaces komen veel voor in de WordPress-output.
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _category_for_list(list_tag) -> str | None:
    """Bepaal of een <ul> bij groente of fruit hoort.

    Het label staat als losse tekst in de omliggende div ("Kistjes Groente:"),
    maar bij fruit is het opgesplitst door een lege <i>: "Kistje<i> </i>Fruit:".
    Daarom wordt teruggelopen over losse strings tot het dichtstbijzijnde label.
    """
    for string in list_tag.find_all_previous(string=True):
        candidate = _normalize_whitespace(str(string))
        if not candidate:
            continue
        if FRUIT_LABEL_RE.search(candidate):
            return "fruit"
        if VEGETABLE_LABEL_RE.search(candidate):
            return "vegetable"
    return None


def _parse_product(list_item, category: str) -> Product | None:
    raw_text = _normalize_whitespace(list_item.get_text())
    if not raw_text:
        return None

    name_tag = list_item.find(["b", "strong"])
    name = _normalize_whitespace(name_tag.get_text()) if name_tag else ""
    if not name:
        raise ParseError("Productnaam ontbreekt in regel: " + repr(raw_text))

    remainder = raw_text
    if remainder.startswith(name):
        remainder = remainder[len(name):]

    excluded = bool(EXCLUSION_MARKER_RE.search(remainder))
    remainder = EXCLUSION_MARKER_RE.sub("", remainder)

    certification_match = CERTIFICATION_RE.search(remainder)
    certification = certification_match.group(1).lower() if certification_match else None
    origin = CERTIFICATION_RE.sub("", remainder)
    origin = LEADING_CONNECTOR_RE.sub("", _normalize_whitespace(origin))

    return Product(
        name=name,
        category=category,
        origin=_normalize_whitespace(origin),
        certification=certification,
        excluded_from_one_person_box=excluded,
        source_line=raw_text,
    )


def parse_content(rendered_html: str) -> list[WeekSection]:
    """Parse content.rendered naar weeksecties, in bronvolgorde."""
    if not rendered_html or not rendered_html.strip():
        raise ParseError("content.rendered is leeg")

    soup = BeautifulSoup(html.unescape(rendered_html), "html.parser")

    # Documentvolgorde bepaalt bij welke week een lijst hoort.
    order = {id(tag): index for index, tag in enumerate(soup.find_all(True))}

    week_markers: list[tuple[int, WeekSection]] = []
    for heading in soup.find_all("h3"):
        match = WEEK_HEADING_RE.match(_normalize_whitespace(heading.get_text()))
        if not match:
            continue
        date_range_text = ""
        next_heading = heading.find_next("h3")
        if next_heading is not None:
            candidate = _normalize_whitespace(next_heading.get_text())
            if not WEEK_HEADING_RE.match(candidate):
                date_range_text = candidate
        heading_text = _normalize_whitespace(heading.get_text())
        excerpt = _normalize_whitespace(
            " ".join(str(sibling) for sibling in heading.next_siblings)
        )[:200]
        week_markers.append(
            (
                order[id(heading)],
                WeekSection(
                    int(match.group(1)),
                    date_range_text,
                    heading_text=heading_text,
                    source_excerpt=excerpt,
                ),
            )
        )

    if not week_markers:
        raise ParseError("Geen enkele weeksectie gevonden in content.rendered")

    week_markers.sort(key=lambda item: item[0])

    for list_tag in soup.find_all("ul"):
        position = order[id(list_tag)]
        owning = [section for start, section in week_markers if start < position]
        if not owning:
            continue  # lijst staat voor de eerste weekkop; hoort niet bij een week
        section = owning[-1]

        category = _category_for_list(list_tag)
        if category is None:
            continue

        for list_item in list_tag.find_all("li"):
            product = _parse_product(list_item, category)
            if product is None:
                continue
            if category == "vegetable":
                section.vegetables.append(product)
            else:
                section.fruit.append(product)

    sections = [section for _, section in week_markers]
    _validate_sections(sections)
    return sections


def _validate_sections(sections: list[WeekSection]) -> None:
    """Harde eisen uit paragraaf 11 van het projectplan."""
    seen: set[int] = set()
    for section in sections:
        if section.week_number in seen:
            raise ParseError(
                "Dubbel weeknummer gevonden: week " + str(section.week_number)
            )
        seen.add(section.week_number)

        if not 1 <= section.week_number <= 53:
            raise ParseError(
                "Ongeldig weeknummer: %d (verwacht 1..53)" % section.week_number
            )
        if not section.date_range_text:
            raise ParseError(
                "Geen geldige datumrange gevonden voor de gedetecteerde "
                "weekkop %r (week %d). De datumrange hoort in de <h3> direct "
                "na de weekkop te staan en is nodig om het ISO-jaar te "
                "bepalen; zonder die range wordt er niets gepubliceerd. "
                "Mogelijk is het bronformaat gewijzigd. Fragment na de kop: "
                "%r"
                % (
                    section.heading_text or ("Week %d" % section.week_number),
                    section.week_number,
                    section.source_excerpt or "<leeg>",
                )
            )

        if not section.vegetables:
            raise ParseError(
                "Week " + str(section.week_number) + " bevat geen groenteproducten"
            )
        if not section.fruit:
            raise ParseError(
                "Week " + str(section.week_number) + " bevat geen fruitproducten"
            )


def quality_warnings(sections: list[WeekSection]) -> list[str]:
    """Zachte controles: afwijkingen melden, maar output niet blokkeren."""
    warnings: list[str] = []
    for section in sections:
        if len(section.vegetables) != 4:
            warnings.append(
                "Week %d: %d groenten gevonden, normaliter 4"
                % (section.week_number, len(section.vegetables))
            )
        if len(section.fruit) != 4:
            warnings.append(
                "Week %d: %d fruitsoorten gevonden, normaliter 4"
                % (section.week_number, len(section.fruit))
            )
    return warnings
