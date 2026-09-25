"""
Tâches planifiées (APScheduler), lancées avec le serveur.

  - chaque jour à 8 h (heure d'Abidjan par défaut) : envoi des bilans mensuels en attente.
    Le 1er du mois, tout le monde reçoit le bilan du mois écoulé ; les jours suivants
    (jusqu'au 7), seuls ceux qui l'auraient manqué le reçoivent.

Désactiver avec ACTIVER_TACHES=false (fait automatiquement pendant les tests).
Heure modifiable avec HEURE_BILAN=8.
"""
import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from database import SessionLocal
from notifications import FUSEAU, envoyer_bilans_du_mois

log = logging.getLogger("finances.taches")


def tache_bilans():
    with SessionLocal() as db:
        try:
            envoyer_bilans_du_mois(db)
        except Exception:  # une erreur ne doit pas arrêter le planificateur
            log.exception("Erreur pendant l'envoi des bilans mensuels")


def demarrer_planificateur():
    if os.environ.get("ACTIVER_TACHES", "true").lower() == "false":
        return None
    planificateur = BackgroundScheduler(timezone=FUSEAU)
    planificateur.add_job(
        tache_bilans,
        CronTrigger(hour=int(os.environ.get("HEURE_BILAN", "8")), minute=0, timezone=FUSEAU),
        id="bilans", misfire_grace_time=3600, coalesce=True,
    )
    # Rattrapage au démarrage : si le serveur était éteint à 8 h, les bilans partent quand même.
    planificateur.add_job(tache_bilans, id="bilans-demarrage", next_run_time=datetime.now(FUSEAU))
    planificateur.start()
    log.info("Planificateur démarré (bilans chaque jour à %sh)", os.environ.get("HEURE_BILAN", "8"))
    return planificateur
