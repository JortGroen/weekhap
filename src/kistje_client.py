"""Consumerclient: haalt de kistinhoud op voor de maaltijdplanner.

Dit is de referentie-implementatie van het consumercontract uit paragraaf 17 van
het projectplan. Read-only HTTP GET naar de publieke GitHub Pages-URL, meer niet:
geen plugin, geen login, geen account, geen state.

Het belangrijkste gedrag is wat er *niet* gebeurt. Bij een mislukte request,
ongeldige JSON of een week die niet exact overeenkomt geeft deze module een
expliciete fout. Er wordt nooit stilzwijgend teruggevallen op een andere week en
er wordt nooit kistinhoud verzonnen -- een verkeerd kistje leidt tot verkeerde
boodschappen, en dat is erger dan geen antwoord.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from src.planner_week import iso_week_key, pickup_date_for_plan_start

DEFAULT_BASE_URL = "https://jortgroen.github.io/weekhap/api/by-week/"

# Toegang is bewust beperkt tot precies deze bron. Een configureerbare base URL
# is handig voor tests en forks, maar mag geen willekeurige host worden.
ALLOWED_SCHEME = "https"
ALLOWED_HOSTS = frozenset({"jortgroen.github.io"})
ALLOWED_PATH_PREFIX = "/weekhap/api/"

TIMEOUT_SECONDS = 10
# Beperkte retry: alleen voor tijdelijke problemen, en kort, want de planner
# wacht op een antwoord.
RETRY_BACKOFF_SECONDS = (1, 3)

SCHEMA_VERSION = 1
EGG_RULE = {
    "name": "Eieren",
    "quantity": 6,
    "unit": "stuks",
    "origin": "planner_rule",
}


class KistjeError(RuntimeError):
    """Basis voor alle expliciete foutstatussen van deze client."""


class KistjeUnavailable(KistjeError):
    """De week kon niet worden opgehaald (netwerk, HTTP-status, ongeldige JSON)."""


class KistjeContractError(KistjeError):
    """De opgehaalde data voldoet niet aan het verwachte contract."""


@dataclass
class BoxContents:
    """De volledige kistinhoud voor een week, klaar voor de planningslogica."""

    week: str
    week_number: int
    date_range_text: str
    vegetables: list[dict]
    fruit: list[dict]
    planner_additions: list[dict]
    source: dict = field(default_factory=dict)
    url: str = ""

    @property
    def produce(self) -> list[dict]:
        """Alle groente en fruit uit het 2-persoonskistje."""
        return self.vegetables + self.fruit

    @property
    def product_names(self) -> list[str]:
        return [item["name"] for item in self.produce]

    def shopping_items(self) -> list[dict]:
        """Alles wat de planner in huis heeft: kistinhoud plus plannerregels."""
        items: list[dict] = []
        for item in self.produce:
            items.append(
                {
                    "name": item["name"],
                    "category": item.get("category"),
                    "source": "kistje",
                }
            )
        for addition in self.planner_additions:
            items.append(
                {
                    "name": addition["name"],
                    "quantity": addition.get("quantity"),
                    "unit": addition.get("unit"),
                    "category": "planner_addition",
                    "source": "planner_rule",
                }
            )
        return items


def week_key_for_plan_start(plan_start: date) -> str:
    """De weeksleutel voor een planstart, via de ophaaldonderdag."""
    return iso_week_key(pickup_date_for_plan_start(plan_start))


def build_url(week_key: str, base_url: str = DEFAULT_BASE_URL) -> str:
    """Bouw en valideer de URL voor een weeksleutel.

    De validatie zit hier en niet pas bij het ophalen, zodat een verkeerd
    geconfigureerde base URL faalt voordat er een request uitgaat.
    """
    if not base_url.endswith("/"):
        base_url += "/"
    url = base_url + week_key + ".json"

    parsed = urlparse(url)
    if parsed.scheme != ALLOWED_SCHEME:
        raise KistjeContractError(
            "Alleen %s is toegestaan, kreeg: %r" % (ALLOWED_SCHEME, parsed.scheme)
        )
    if parsed.hostname not in ALLOWED_HOSTS:
        raise KistjeContractError(
            "Host %r staat niet op de toegestane lijst %s"
            % (parsed.hostname, sorted(ALLOWED_HOSTS))
        )
    if not parsed.path.startswith(ALLOWED_PATH_PREFIX):
        raise KistjeContractError(
            "Pad %r valt buiten %r" % (parsed.path, ALLOWED_PATH_PREFIX)
        )
    return url


def _http_get_json(url: str, timeout: int, backoffs: tuple[int, ...], sleep) -> dict:
    """Read-only GET met beperkte retry op uitsluitend tijdelijke fouten.

    Een 404 wordt niet opnieuw geprobeerd: die betekent dat de week simpelweg
    niet gepubliceerd is, en herhalen verandert daar niets aan.
    """
    attempts = len(backoffs) + 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                method="GET",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "maaltijd-planner-kistje-client/1.0",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise KistjeUnavailable(
                        "Verwachtte HTTP 200, kreeg %d voor %s" % (response.status, url)
                    )
                raw = response.read().decode("utf-8")
            try:
                payload = json.loads(raw)
            except ValueError as exc:
                raise KistjeUnavailable("Ongeldige JSON van %s: %s" % (url, exc)) from exc
            if not isinstance(payload, dict):
                raise KistjeUnavailable(
                    "Verwachtte een JSON-object van %s, kreeg %s"
                    % (url, type(payload).__name__)
                )
            return payload

        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise KistjeUnavailable(
                    "Week niet gepubliceerd (HTTP 404): %s" % url
                ) from exc
            last_error = KistjeUnavailable("HTTP %d voor %s" % (exc.code, url))
        except KistjeUnavailable as exc:
            last_error = exc
        except Exception as exc:  # noqa: BLE001 - timeout, DNS, TLS: tijdelijk
            last_error = KistjeUnavailable("Netwerkfout bij %s: %s" % (url, exc))

        if attempt <= len(backoffs):
            sleep(backoffs[attempt - 1])

    raise KistjeUnavailable(
        "Ophalen van %s mislukt na %d pogingen: %s" % (url, attempts, last_error)
    )


def _validate_document(document: dict, expected_week: str, url: str) -> None:
    """Controleer het contract voordat de data de planningslogica in gaat."""
    schema_version = document.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise KistjeContractError(
            "Onbekende schema_version %r (verwacht %d) in %s"
            % (schema_version, SCHEMA_VERSION, url)
        )

    # De kern van de veiligheid: een bestand mag nooit voor een andere week
    # doorgaan dan is opgevraagd.
    actual_week = document.get("week")
    if actual_week != expected_week:
        raise KistjeContractError(
            "Weeksleutel komt niet overeen: gevraagd %r, gekregen %r uit %s"
            % (expected_week, actual_week, url)
        )

    source = document.get("source")
    if not isinstance(source, dict) or not source.get("modified"):
        raise KistjeContractError("Freshnessveld source.modified ontbreekt in %s" % url)

    for field_name in ("vegetables", "fruit"):
        items = document.get(field_name)
        if not isinstance(items, list) or not items:
            raise KistjeContractError(
                "Veld %r ontbreekt of is leeg in %s" % (field_name, url)
            )
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                raise KistjeContractError(
                    "Product zonder naam in %r van %s" % (field_name, url)
                )


def _planner_additions(document: dict) -> list[dict]:
    """De vaste plannerregel van 6 eieren, zonder te dupliceren.

    De pijplijn publiceert deze al mee. Ontbreekt hij toch, dan past de client de
    regel alsnog toe -- de regel hoort bij de planner, niet bij de bron.
    """
    additions = [
        dict(item)
        for item in document.get("planner_additions", [])
        if isinstance(item, dict) and item.get("name")
    ]
    eggs = [item for item in additions if item["name"].lower() == "eieren"]

    if not eggs:
        additions.append(dict(EGG_RULE))
        return additions

    if len(eggs) > 1:
        raise KistjeContractError("Meerdere eierregels gevonden in planner_additions")
    if eggs[0].get("quantity") != 6:
        raise KistjeContractError(
            "Verwachtte 6 eieren volgens de plannerregel, kreeg %r"
            % eggs[0].get("quantity")
        )
    return additions


def fetch_week(
    week_key: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = TIMEOUT_SECONDS,
    backoffs: tuple[int, ...] = RETRY_BACKOFF_SECONDS,
    sleep=time.sleep,
) -> BoxContents:
    """Haal een specifieke week op en valideer die volledig."""
    url = build_url(week_key, base_url)
    document = _http_get_json(url, timeout, backoffs, sleep)
    _validate_document(document, week_key, url)

    return BoxContents(
        week=document["week"],
        week_number=document.get("week_number", 0),
        date_range_text=document.get("date_range_text", ""),
        vegetables=list(document["vegetables"]),
        fruit=list(document["fruit"]),
        planner_additions=_planner_additions(document),
        source=dict(document.get("source", {})),
        url=url,
    )


def get_box_for_plan_start(
    plan_start: date,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = TIMEOUT_SECONDS,
    backoffs: tuple[int, ...] = RETRY_BACKOFF_SECONDS,
    sleep=time.sleep,
) -> BoxContents:
    """De volledige flow: planstart -> ophaaldonderdag -> week -> kistinhoud."""
    return fetch_week(
        week_key_for_plan_start(plan_start),
        base_url=base_url,
        timeout=timeout,
        backoffs=backoffs,
        sleep=sleep,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Haal de kistinhoud op voor een planstart of weeksleutel"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan-start", help="Startdatum van de planning (YYYY-MM-DD)")
    group.add_argument("--week", help="Expliciete weeksleutel, bijvoorbeeld 2026-W35")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)

    try:
        if args.week:
            box = fetch_week(args.week, base_url=args.base_url)
        else:
            plan_start = (
                date.fromisoformat(args.plan_start) if args.plan_start else date.today()
            )
            print("Planstart: %s" % plan_start)
            print("Ophaaldonderdag: %s" % pickup_date_for_plan_start(plan_start))
            box = get_box_for_plan_start(plan_start, base_url=args.base_url)
    except KistjeError as exc:
        print("FOUT: %s: %s" % (type(exc).__name__, exc))
        return 1

    print("URL: %s" % box.url)
    print("Week: %s (%s)" % (box.week, box.date_range_text))
    print("Bron gewijzigd: %s" % box.source.get("modified"))
    print("Groente: %s" % ", ".join(item["name"] for item in box.vegetables))
    print("Fruit:   %s" % ", ".join(item["name"] for item in box.fruit))
    for addition in box.planner_additions:
        print(
            "Plannerregel: %s %s %s"
            % (addition.get("quantity"), addition.get("unit"), addition["name"])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
