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

from src.normalize_kistje import PAGE_ID, SCHEMA_VERSION, compute_content_hash
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


def _short(value) -> str:
    """Hashes afkorten zodat logregels leesbaar blijven."""
    if not value:
        return "<geen>"
    text = str(value)
    return text[:12] + "..." if len(text) > 12 else text


def verify_week_document(
    document: dict, expected_key: str, expected_hash: str = ""
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
    # Integriteit: de meegeleverde hash moet de inhoud daadwerkelijk dekken.
    served_hash = document.get("content_hash")
    if not served_hash:
        raise VerificationError("Week %s heeft geen content_hash" % expected_key)
    recomputed = compute_content_hash(document)
    if recomputed != served_hash:
        raise VerificationError(
            "Week %s: content_hash %s dekt de inhoud niet (herberekend %s)"
            % (expected_key, _short(served_hash), _short(recomputed))
        )

    # Versheid: dit moet exact de publicatie van deze run zijn. Een oudere maar
    # geldige versie heeft een andere hash en wordt hier geweigerd.
    if expected_hash and served_hash != expected_hash:
        raise VerificationError(
            "Week %s serveert content_hash %s, verwacht %s: Pages levert nog "
            "een oudere versie" % (expected_key, _short(served_hash), _short(expected_hash))
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
    expected_hash: str = "",
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
    if not expected_hash:
        return status

    attempts = len(backoffs) + 1
    for attempt in range(1, attempts + 1):
        served = status.get("publication_hash")
        if served == expected_hash:
            if attempt > 1:
                print("Pages is bijgewerkt na %d pogingen." % attempt, flush=True)
            return status

        if attempt > len(backoffs):
            break
        delay = backoffs[attempt - 1]
        print(
            "Pages serveert nog publication_hash=%s, verwacht %s; "
            "opnieuw over %ds" % (_short(served), _short(expected_hash), delay),
            flush=True,
        )
        sleep(delay)
        status = fetch_json(base_url + "/api/status.json", (), sleep)

    raise VerificationError(
        "Pages serveert nog steeds publication_hash=%s terwijl deze run %s "
        "publiceerde. De nieuwe publicatie is niet doorgekomen; een oudere "
        "maar geldige versie telt niet als geslaagd."
        % (_short(status.get("publication_hash")), _short(expected_hash))
    )


def main(argv: list[str] | None = None) -> int:
    base_url = default_base_url()
    expected_hash = os.environ.get("EXPECTED_PUBLICATION_HASH", "").strip()
    print("Verifieren via publieke URL: " + base_url, flush=True)
    if expected_hash:
        print("Verwachte publication_hash: " + expected_hash, flush=True)
    else:
        print(
            "Geen verwachte hash meegegeven; alleen het contract wordt "
            "gecontroleerd, niet de versheid.",
            flush=True,
        )

    try:
        status = fetch_fresh_status(base_url, expected_hash)
        published_weeks = status.get("published_weeks") or []
        if not published_weeks:
            raise VerificationError("status.json noemt geen gepubliceerde weken")
        print("status.json meldt weken: " + ", ".join(published_weeks), flush=True)

        # De weekhashes uit status.json horen bij dezelfde publicatie, dus zij
        # bepalen welke versie ieder weekbestand moet hebben.
        week_hashes = status.get("week_hashes") or {}
        if expected_hash and not week_hashes:
            raise VerificationError("status.json bevat geen week_hashes")

        for week_key in published_weeks:
            url = "%s/api/by-week/%s.json" % (base_url, week_key)
            document = fetch_json(url)
            verify_week_document(document, week_key, week_hashes.get(week_key, ""))
            print("  OK %s (%d groente, %d fruit, hash %s)" % (
                week_key,
                len(document["vegetables"]),
                len(document["fruit"]),
                _short(document.get("content_hash")),
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

    if expected_hash:
        print(
            "Versheid bewezen: Pages serveert publication_hash %s, exact wat "
            "deze run genereerde." % _short(expected_hash)
        )
    print("E2E-verificatie geslaagd: publieke data voldoet aan het contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
