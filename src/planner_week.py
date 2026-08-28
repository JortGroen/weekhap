"""Weekselectie voor de maaltijdplanner.

Een kistje wordt op donderdag opgehaald en mag vanaf dat moment worden gebruikt.
De relevante kistweek is daarom de ISO-week van de ophaaldonderdag, niet de
ISO-week van de planstart zelf. Start een plan op maandag, dan hoort het kistje
van de donderdag daarvoor erbij.
"""

from __future__ import annotations

from datetime import date, timedelta

THURSDAY = 3  # date.weekday(): maandag = 0


def pickup_date_for_plan_start(plan_start: date) -> date:
    """De meest recente donderdag op of voor de planstart.

    Valt de planstart zelf op donderdag, dan is dat de ophaaldag.
    """
    if not isinstance(plan_start, date):
        raise TypeError("plan_start moet een datetime.date zijn")
    offset = (plan_start.weekday() - THURSDAY) % 7
    return plan_start - timedelta(days=offset)


def iso_week_key(day: date) -> str:
    """Sleutel als '2026-W35', op basis van ISO-jaar en ISO-week.

    Bewust het ISO-jaar en niet het kalenderjaar: rond de jaarwisseling lopen
    die uiteen, en dan zou 2026-W01 met 2025-W01 kunnen botsen.
    """
    iso_year, iso_week, _ = day.isocalendar()
    return "%04d-W%02d" % (iso_year, iso_week)


def week_key_for_plan_start(plan_start: date) -> str:
    """De weeksleutel die de planner moet opvragen voor een gegeven planstart."""
    return iso_week_key(pickup_date_for_plan_start(plan_start))
