"""Calcul des fenêtres temporelles matin/soir. Voir PRD §S1."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Toronto")

# Heures cibles de livraison (le script tourne ~15s avant)
MORNING_DELIVERY = time(6, 45)
EVENING_DELIVERY = time(17, 30)

# Bornes de fenêtre temporelle
MORNING_WINDOW_END = time(6, 30)  # le matin couvre jusqu'à 6h30
EVENING_WINDOW_PIVOT = time(17, 15)  # le soir couvre jusqu'à 17h15


def compute_window(
    moment: Literal["matin", "soir"],
    now: datetime,
) -> tuple[datetime, datetime]:
    """
    Retourne (window_start, window_end) en TZ America/Toronto.

    - matin (6h45) : couvre [hier 17h30 → aujourd'hui 6h30]
    - soir (17h30) : couvre [aujourd'hui 6h30 → aujourd'hui 17h15]
    """
    now_local = now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)
    today = now_local.date()

    if moment == "matin":
        end = datetime.combine(today, MORNING_WINDOW_END, tzinfo=TZ)
        start = datetime.combine(today - timedelta(days=1), EVENING_DELIVERY, tzinfo=TZ)
    elif moment == "soir":
        end = datetime.combine(today, EVENING_WINDOW_PIVOT, tzinfo=TZ)
        start = datetime.combine(today, MORNING_WINDOW_END, tzinfo=TZ)
    else:
        raise ValueError(f"moment doit être 'matin' ou 'soir', reçu : {moment}")

    return start, end


def briefing_id(moment: Literal["matin", "soir"], now: datetime) -> str:
    """ID stable type '2026-04-19-matin'."""
    now_local = now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)
    return f"{now_local.date().isoformat()}-{moment}"
