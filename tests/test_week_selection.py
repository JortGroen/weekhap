"""Tests voor ophaaldonderdag, ISO-weeksleutel en jaarinferentie."""

from __future__ import annotations

from datetime import date

import pytest

from src.planner_week import (
    iso_week_key,
    pickup_date_for_plan_start,
    week_key_for_plan_start,
)


# --- Verplichte testcases uit paragraaf 13 van het projectplan --------------


@pytest.mark.parametrize(
    "plan_start, expected_pickup, expected_key",
    [
        (date(2026, 8, 27), date(2026, 8, 27), "2026-W35"),  # planstart is donderdag
        (date(2026, 8, 31), date(2026, 8, 27), "2026-W35"),  # planstart is maandag
        (date(2026, 9, 3), date(2026, 9, 3), "2026-W36"),
    ],
)
def test_verplichte_plannercases(plan_start, expected_pickup, expected_key):
    assert pickup_date_for_plan_start(plan_start) == expected_pickup
    assert week_key_for_plan_start(plan_start) == expected_key


def test_planstart_donderdag_gebruikt_dezelfde_dag():
    plan_start = date(2026, 8, 27)
    assert plan_start.weekday() == 3
    assert pickup_date_for_plan_start(plan_start) == plan_start


@pytest.mark.parametrize(
    "plan_start, expected_pickup",
    [
        (date(2026, 8, 28), date(2026, 8, 27)),  # vrijdag -> donderdag ervoor
        (date(2026, 8, 29), date(2026, 8, 27)),  # zaterdag
        (date(2026, 8, 30), date(2026, 8, 27)),  # zondag
        (date(2026, 9, 1), date(2026, 8, 27)),   # dinsdag
        (date(2026, 9, 2), date(2026, 8, 27)),   # woensdag: donderdag ervoor
    ],
)
def test_meest_recente_donderdag_voor_alle_weekdagen(plan_start, expected_pickup):
    assert pickup_date_for_plan_start(plan_start) == expected_pickup


def test_pickup_ligt_nooit_in_de_toekomst():
    for offset in range(400):
        plan_start = date(2026, 1, 1).toordinal() + offset
        plan_start = date.fromordinal(plan_start)
        pickup = pickup_date_for_plan_start(plan_start)
        assert pickup <= plan_start
        assert (plan_start - pickup).days < 7
        assert pickup.weekday() == 3


# --- Jaarwisselingen -------------------------------------------------------


def test_iso_week_key_gebruikt_iso_jaar_niet_kalenderjaar():
    # 31 december 2025 valt in ISO-week 1 van 2026.
    day = date(2025, 12, 31)
    assert day.isocalendar()[0] == 2026
    assert iso_week_key(day) == "2026-W01"


def test_planstart_begin_januari_pakt_donderdag_in_vorig_kalenderjaar():
    # Maandag 5 januari 2026 -> donderdag 1 januari 2026, ISO-week 2026-W01.
    plan_start = date(2026, 1, 5)
    assert pickup_date_for_plan_start(plan_start) == date(2026, 1, 1)
    assert week_key_for_plan_start(plan_start) == "2026-W01"


def test_planstart_over_jaargrens_heen():
    # Maandag 29 december 2025 -> donderdag 25 december 2025 (ISO 2025-W52).
    plan_start = date(2025, 12, 29)
    assert pickup_date_for_plan_start(plan_start) == date(2025, 12, 25)
    assert week_key_for_plan_start(plan_start) == "2025-W52"


# --- Discovery-fallback ----------------------------------------------------


def test_fallback_gebruikt_slug_route_als_id_faalt():
    """Valt de primaire bron weg, dan moet de slug-route het overnemen."""
    from src.fetch_kistje import FetchError, fetch_with_fallback

    class _Response:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

    class _Session:
        def __init__(self):
            self.urls = []

        def get(self, url, headers=None, timeout=None):
            self.urls.append(url)
            if "pages/19172" in url:
                return _Response(500, {})
            return _Response(200, [{"id": 19172, "slug": "deze-week-in-je-kistje"}])

    session = _Session()
    page = fetch_with_fallback(
        backoffs=(), sleep=lambda _: None, session=session
    )

    assert page["id"] == 19172
    assert any("slug=" in url for url in session.urls)


def test_fallback_faalt_expliciet_als_beide_routes_falen():
    from src.fetch_kistje import FetchError, fetch_with_fallback

    class _Session:
        def get(self, url, headers=None, timeout=None):
            raise OSError("netwerk plat")

    with pytest.raises(FetchError, match="Zowel de primaire bron als discovery"):
        fetch_with_fallback(backoffs=(), sleep=lambda _: None, session=_Session())


# --- Weigeringen (401/403/429) --------------------------------------------


class _StatusSession:
    """Session-stub die altijd dezelfde HTTP-status teruggeeft."""

    def __init__(self, status):
        self.status = status
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1

        class _Response:
            status_code = self.status

            def json(self_inner):
                return {}

        return _Response()


@pytest.mark.parametrize("status", [401, 403, 429])
def test_weigering_wordt_niet_geretryd(status):
    """Een weigering is geen storing: herhalen helpt niet en verergert een rate-limit."""
    from src.fetch_kistje import FetchBlocked, fetch_page

    session = _StatusSession(status)
    with pytest.raises(FetchBlocked, match="weigert het verzoek"):
        fetch_page(backoffs=(10, 30, 90), sleep=lambda _: None, session=session)

    assert session.calls == 1, "weigering mag maar een keer worden opgevraagd"


def test_weigering_slaat_discovery_over():
    """Discovery loopt over dezelfde host en wordt dus net zo geweigerd."""
    from src.fetch_kistje import FetchBlocked, fetch_with_fallback

    session = _StatusSession(403)
    with pytest.raises(FetchBlocked):
        fetch_with_fallback(backoffs=(10,), sleep=lambda _: None, session=session)

    assert session.calls == 1


def test_serverfout_wordt_wel_geretryd():
    """Een 500 is wel een storing en verdient de volledige retry."""
    from src.fetch_kistje import FetchError, fetch_page

    session = _StatusSession(500)
    with pytest.raises(FetchError):
        fetch_page(backoffs=(1, 2), sleep=lambda _: None, session=session)

    assert session.calls == 3
