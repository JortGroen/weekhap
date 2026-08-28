"""Live E2E-verificatie tegen de publieke GitHub Pages-URL.

De pijplijn telt pas als geslaagd wanneer de data opnieuw is opgehaald via het
publieke eindpunt -- niet vanuit de repository-werkmap. Deze module doet dus
bewust een echte HTTPS-call naar github.io en parseert het resultaat opnieuw.

GitHub Pages heeft na een push even nodig om te herbouwen, dus er wordt met
backoff opnieuw geprobeerd voordat de verificatie faalt.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date

import requests

from src.normalize_kistje import PAGE_ID, SCHEMA_VERSION
from src.planner_week import iso_week_key

TIMEOUT_SECONDS = 20
# Pages-deploys duren doorgaans onder de minuut; dit geeft ruim vijf minuten.
BACKOFF_SECONDS = (15, 30, 60, 90, 120)


class VerificationError(RuntimeError):
    """De publiek gepubliceerde data voldoet niet aan het contract."""


def default_base_url() -> str:
    """Leid de Pages-URL af uit GITHUB_REPOSITORY, tenzij expliciet gezet."""
    explicit = os.environ.get("PAGES_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" not in repository:
        raise VerificationError(
            "Kan de Pages-URL niet bepalen. Zet repository variable "
            "PAGES_BASE_URL, bijvoorbeeld https://gebruiker.github.io/repo"
        )
    owner, repo = repository.split("/", 1)
    return "https://%s.github.io/%s" % (owner.lower(), repo)


def fetch_json(url: str, backoffs: tuple[int, ...] = BACKOFF_SECONDS, sleep=time.sleep):
    """Haal JSON op van een publieke URL, met geduld voor de Pages-deploy."""
    attempts = len(backoffs) + 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                timeout=TIMEOUT_SECONDS,
                headers={
                    "Accept": "application/json",
                    # Pages en tussenliggende caches mogen geen oude versie leveren.
                    "Cache-Control": "no-cache",
                },
            )
            if not 200 <= response.status_code < 300:
                raise VerificationError(
                    "HTTP %d bij %s" % (response.status_code, url)
                )
            return response.json()
        except Exception as exc:  # noqa: BLE001 - Pages kan nog aan het bouwen zijn
            last_error = exc
            if attempt <= len(backoffs):
                delay = backoffs[attempt - 1]
                print(
                    "Nog niet beschikbaar (%s); opnieuw over %ds" % (exc, delay),
                    flush=True,
                )
                sleep(delay)

    raise VerificationError(
        "Publieke URL %s bleef onbereikbaar na %d pogingen: %s"
        % (url, attempts, last_error)
    )


def verify_week_document(
    document: dict, expected_key: str, expected_modified: str = ""
) -> None:
    """Controleer het contract dat de maaltijdplanner mag verwachten."""
    if document.get("schema_version") != SCHEMA_VERSION:
        raise VerificationError(
            "Onverwachte schema_version: %r" % document.get("schema_version")
        )

    # Een oude week mag nooit als een andere week worden geinterpreteerd.
    if document.get("week") != expected_key:
        raise VerificationError(
            "Weeksleutel komt niet overeen: %r in bestand voor %r"
            % (document.get("week"), expected_key)
        )

    source = document.get("source") or {}
    if source.get("page_id") != PAGE_ID:
        raise VerificationError("Onverwacht source.page_id: %r" % source.get("page_id"))
    if not source.get("modified"):
        raise VerificationError("source.modified ontbreekt")
    if not source.get("fetched_at"):
        raise VerificationError("source.fetched_at ontbreekt")
    # Het weekbestand moet uit dezelfde publicatie komen als status.json.
    if expected_modified and source.get("modified") != expected_modified:
        raise VerificationError(
            "Week %s serveert source.modified=%r, verwacht %r"
            % (expected_key, source.get("modified"), expected_modified)
        )

    if not document.get("vegetables"):
        raise VerificationError("Week %s bevat geen groente" % expected_key)
    if not document.get("fruit"):
        raise VerificationError("Week %s bevat geen fruit" % expected_key)

    eggs = [
        item
        for item in document.get("planner_additions", [])
        if item.get("name") == "Eieren"
    ]
    if len(eggs) != 1 or eggs[0].get("quantity") != 6:
        raise VerificationError(
            "Verwachte plannerregel van 6 eieren ontbreekt in %s" % expected_key
        )


def fetch_fresh_status(
    base_url: str,
    expected_modified: str = "",
    backoffs: tuple[int, ...] = BACKOFF_SECONDS,
    sleep=time.sleep,
) -> dict:
    """Haal status.json op en wacht tot Pages de zojuist gepubliceerde versie serveert.

    Zonder deze controle valideert de job mogelijk nog de vorige versie: Pages
    herbouwt asynchroon na de push, dus een geslaagde verificatie zou niets
    zeggen over wat er net is gepubliceerd. Is `expected_modified` leeg -- bij
    een handmatige run buiten de workflow -- dan wordt alleen opgehaald.
    """
    status = fetch_json(base_url + "/api/status.json", backoffs, sleep)
    if not expected_modified:
        return status

    attempts = len(backoffs) + 1
    for attempt in range(1, attempts + 1):
        served = status.get("source_modified")
        if served == expected_modified:
            if attempt > 1:
                print("Pages is bijgewerkt na %d pogingen." % attempt, flush=True)
            return status

        if attempt > len(backoffs):
            break
        delay = backoffs[attempt - 1]
        print(
            "Pages serveert nog source_modified=%r, verwacht %r; "
            "opnieuw over %ds" % (served, expected_modified, delay),
            flush=True,
        )
        sleep(delay)
        status = fetch_json(base_url + "/api/status.json", (), sleep)

    raise VerificationError(
        "Pages serveert nog steeds source_modified=%r terwijl de run %r "
        "publiceerde. De publicatie is niet doorgekomen."
        % (status.get("source_modified"), expected_modified)
    )


def main(argv: list[str] | None = None) -> int:
    base_url = default_base_url()
    expected_modified = os.environ.get("EXPECTED_SOURCE_MODIFIED", "").strip()
    print("Verifieren via publieke URL: " + base_url, flush=True)
    if expected_modified:
        print("Verwachte source_modified: " + expected_modified, flush=True)

    try:
        status = fetch_fresh_status(base_url, expected_modified)
        published_weeks = status.get("published_weeks") or []
        if not published_weeks:
            raise VerificationError("status.json noemt geen gepubliceerde weken")
        print("status.json meldt weken: " + ", ".join(published_weeks), flush=True)

        for week_key in published_weeks:
            url = "%s/api/by-week/%s.json" % (base_url, week_key)
            document = fetch_json(url)
            verify_week_document(document, week_key, expected_modified)
            print("  OK %s (%d groente, %d fruit)" % (
                week_key, len(document["vegetables"]), len(document["fruit"])
            ), flush=True)

        latest = fetch_json(base_url + "/api/latest.json")
        if (latest.get("source") or {}).get("page_id") != PAGE_ID:
            raise VerificationError("latest.json verwijst niet naar page %d" % PAGE_ID)

        # De week die vandaag relevant zou zijn, hoort gepubliceerd te zijn.
        current_key = iso_week_key(date.today())
        if current_key not in published_weeks:
            print(
                "LET OP: huidige week %s staat niet in de publicatie (%s)"
                % (current_key, ", ".join(published_weeks)),
                file=sys.stderr,
            )
    except VerificationError as exc:
        print("E2E-VERIFICATIE MISLUKT: %s" % exc, file=sys.stderr)
        return 1

    if expected_modified:
        print(
            "Versheid bevestigd: Pages serveert de publicatie van deze run."
        )
    print("E2E-verificatie geslaagd: publieke data voldoet aan het contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
