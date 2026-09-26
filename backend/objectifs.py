"""
Objectifs d'épargne : progression, reste à épargner et montant conseillé par mois.

Un versement vers un objectif est une dépense de la catégorie « Épargne » liée à l'objectif :
il compte donc aussi dans le taux d'épargne et le fonds d'urgence.
"""
from datetime import date
from math import ceil
from typing import List

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from models import Mouvement, MouvementRecurrent, ObjectifEpargne, User


def mois_restants(aujourdhui: date, limite: date) -> int:
    """Nombre de mois pour épargner, mois en cours compris (septembre -> décembre = 4)."""
    if limite < aujourdhui:
        return 0
    return (limite.year * 12 + limite.month) - (aujourdhui.year * 12 + aujourdhui.month) + 1


def decrire(o: ObjectifEpargne, epargne: int, aujourdhui: date, versement_auto: int = 0) -> dict:
    reste = max(o.montant_cible - epargne, 0)
    mois = mois_restants(aujourdhui, o.date_limite) if o.date_limite else None
    if reste == 0:
        statut = "atteint"
    elif o.date_limite and o.date_limite < aujourdhui:
        statut = "echeance_depassee"
    else:
        statut = "en_cours"
    return {
        "id": o.id, "nom": o.nom, "montant_cible": o.montant_cible,
        "date_limite": o.date_limite.isoformat() if o.date_limite else None,
        "epargne": epargne, "reste": reste,
        "pourcentage": min(round(epargne * 100 / o.montant_cible), 100) if o.montant_cible else 0,
        "mois_restants": mois,
        "par_mois_conseille": ceil(reste / mois) if mois and reste else None,
        "statut": statut,
        "versement_auto": versement_auto,  # montant versé chaque mois par des mouvements récurrents
    }


def objectifs_de(db: Session, user: User, aujourdhui: date) -> List[dict]:
    """Objectifs en cours avec leur progression et leurs versements automatiques (2 requêtes)."""
    lignes = (db.query(ObjectifEpargne, func.coalesce(func.sum(Mouvement.montant), 0))
              .outerjoin(Mouvement, and_(Mouvement.objectif_id == ObjectifEpargne.id, Mouvement.archive.is_(False)))
              .filter(ObjectifEpargne.user_id == user.id, ObjectifEpargne.archive.is_(False))
              .group_by(ObjectifEpargne.id)
              .order_by(ObjectifEpargne.date_limite.is_(None), ObjectifEpargne.date_limite, ObjectifEpargne.id)
              .all())
    if not lignes:
        return []
    auto = dict(db.query(MouvementRecurrent.objectif_id, func.sum(MouvementRecurrent.montant))
                .filter(MouvementRecurrent.user_id == user.id, MouvementRecurrent.actif.is_(True),
                        MouvementRecurrent.objectif_id.isnot(None))
                .group_by(MouvementRecurrent.objectif_id).all())
    return [decrire(o, int(total), aujourdhui, int(auto.get(o.id, 0))) for o, total in lignes]
