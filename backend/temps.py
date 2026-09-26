"""Date du jour dans le fuseau horaire de l'utilisateur (Abidjan par défaut)."""
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

FUSEAU = ZoneInfo(os.environ.get("FUSEAU_HORAIRE", "Africa/Abidjan"))


def aujourd_hui() -> date:
    return datetime.now(FUSEAU).date()
