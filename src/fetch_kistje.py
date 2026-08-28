"""Live ophalen van de Hoeve Biesland WordPress REST API.

De bron is publiek: geen API-key, geen repository secret. Een tijdelijke
netwerkfout mag nooit de laatst bekende goede dataset overschrijven, dus deze
module gooit bij falen een exceptie in plaats van lege of halve data terug te
geven.
"""

from __future__ import annotations

import json
import time

import requests

PAGE_ID = 19172
API_URL = "https://hoevebiesland.nl/wp-json/wp/v2/pages/%d" % PAGE_ID
DISCOVERY_URL = (
    "https://hoevebiesland.nl/wp-json/wp/v2/pages?slug=deze-week-in-je-kistje"
)

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "maaltijd-planner-kistje-fetch/1.0",
}

TIMEOUT_SECONDS = 20
# Drie pogingen met oplopende backoff; ruim genoeg voor een korte storing bij de
# bron zonder de workflow onnodig lang te laten hangen.
BACKOFF_SECONDS = (10, 30, 90)


class FetchError(RuntimeError):
    """De bron kon niet betrouwbaar worden opgehaald."""


def fetch_page(
    url: str = API_URL,
    timeout: int = TIMEOUT_SECONDS,
    backoffs: tuple[int, ...] = BACKOFF_SECONDS,
    sleep=time.sleep,
    session=None,
) -> dict:
    """Haal de pagina op en geef de geparste JSON terug.

    Alleen HTTP 2xx wordt geaccepteerd. Na de laatste poging wordt de
    oorspronkelijke fout doorgegeven, zodat de workflow zichtbaar faalt.
    """
    client = session or requests
    attempts = len(backoffs) + 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, headers=HEADERS, timeout=timeout)
            if not 200 <= response.status_code < 300:
                raise FetchError(
                    "Onverwachte HTTP-status %d van %s" % (response.status_code, url)
                )
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise FetchError("Response is geen geldige JSON") from exc
            if not isinstance(payload, dict):
                raise FetchError(
                    "Response is geen JSON-object maar %s" % type(payload).__name__
                )
            return payload
        except Exception as exc:  # noqa: BLE001 - elke fout is een retry waard
            last_error = exc
            if attempt <= len(backoffs):
                delay = backoffs[attempt - 1]
                print(
                    "Poging %d/%d mislukt (%s); opnieuw over %ds"
                    % (attempt, attempts, exc, delay)
                )
                sleep(delay)

    raise FetchError(
        "Ophalen van %s mislukt na %d pogingen: %s" % (url, attempts, last_error)
    ) from last_error


def fetch_via_discovery(
    url: str = DISCOVERY_URL,
    timeout: int = TIMEOUT_SECONDS,
    backoffs: tuple[int, ...] = BACKOFF_SECONDS,
    sleep=time.sleep,
    session=None,
) -> dict:
    """Zoek de pagina op slug in plaats van op id.

    Vangnet voor het geval het page-id ooit verandert. De endpoint geeft een
    lijst terug, dus die wordt hier tot een enkele pagina teruggebracht.
    """
    client = session or requests
    attempts = len(backoffs) + 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, headers=HEADERS, timeout=timeout)
            if not 200 <= response.status_code < 300:
                raise FetchError(
                    "Onverwachte HTTP-status %d van %s" % (response.status_code, url)
                )
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                raise FetchError("Discovery gaf geen resultaten terug")
            page = payload[0]
            if not isinstance(page, dict):
                raise FetchError("Discovery gaf geen paginaobject terug")
            return page
        except Exception as exc:  # noqa: BLE001 - elke fout is een retry waard
            last_error = exc
            if attempt <= len(backoffs):
                sleep(backoffs[attempt - 1])

    raise FetchError(
        "Discovery via %s mislukt na %d pogingen: %s" % (url, attempts, last_error)
    ) from last_error


def fetch_with_fallback(
    url: str = API_URL,
    discovery_url: str = DISCOVERY_URL,
    timeout: int = TIMEOUT_SECONDS,
    backoffs: tuple[int, ...] = BACKOFF_SECONDS,
    sleep=time.sleep,
    session=None,
) -> dict:
    """Haal de pagina op id op; lukt dat niet, probeer dan de slug-route."""
    try:
        return fetch_page(url, timeout, backoffs, sleep, session)
    except FetchError as primary_error:
        print("Primaire bron mislukt (%s); discovery via slug wordt geprobeerd"
              % primary_error)
        try:
            return fetch_via_discovery(discovery_url, timeout, backoffs, sleep, session)
        except FetchError as discovery_error:
            raise FetchError(
                "Zowel de primaire bron als discovery mislukten. "
                "Primair: %s. Discovery: %s" % (primary_error, discovery_error)
            ) from discovery_error


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Haal de kistje-pagina live op")
    parser.add_argument("--out", help="Schrijf de ruwe response naar dit bestand")
    args = parser.parse_args()

    payload = fetch_with_fallback()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    print("Opgehaald: id=%s modified=%s" % (payload.get("id"), payload.get("modified")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
