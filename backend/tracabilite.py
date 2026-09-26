"""
Journal de traçabilité : chaque création, modification ou archivage y est inscrit,
avec la valeur avant et après (en JSON).
"""
import json
from typing import Optional

from sqlalchemy.orm import Session

from models import JournalAudit, Mouvement, User


def vers_dict(m: Mouvement) -> dict:
    return {"type": m.type, "montant": m.montant, "libelle": m.libelle, "date": m.date.isoformat(),
            "categorie": m.categorie.nom if m.categorie else None, "archive": m.archive}


def journaliser(db: Session, user: User, action: str, entite: str, entite_id: int,
                avant: Optional[dict] = None, apres: Optional[dict] = None):
    db.add(JournalAudit(user_id=user.id, action=action, entite=entite, entite_id=entite_id,
                        avant=json.dumps(avant, ensure_ascii=False) if avant else None,
                        apres=json.dumps(apres, ensure_ascii=False) if apres else None))
