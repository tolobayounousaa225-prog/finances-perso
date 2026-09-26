"""
Mouvements récurrents : salaire, loyer, abonnements… créés automatiquement chaque mois.

La création a lieu :
  - à l'ouverture de l'application (appel de /api/me) ;
  - chaque jour avec la tâche planifiée (Vercel Cron ou APScheduler) ;
  - dès qu'un mouvement récurrent est ajouté ou modifié.
Un mois oublié (application pas ouverte, serveur éteint) est rattrapé la fois suivante.
"""
from calendar import monthrange
from datetime import date
from typing import Iterator, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import GenerationRecurrente, Mouvement, MouvementRecurrent, User
from notifications import aujourd_hui, verifier_alerte_budget
from tracabilite import journaliser, vers_dict


def date_du_mois(annee: int, mois: int, jour: int) -> date:
    """Le jour choisi, ou le dernier jour du mois s'il est plus court (31 -> 28 février)."""
    return date(annee, mois, min(jour, monthrange(annee, mois)[1]))


def occurrences(r: MouvementRecurrent, jusqu_a: date) -> Iterator[Tuple[str, date]]:
    """Les (période, date) dues entre le début du récurrent et `jusqu_a` inclus."""
    a, m = r.debut.year, r.debut.month
    while (a, m) <= (jusqu_a.year, jusqu_a.month):
        d = date_du_mois(a, m, r.jour)
        if r.debut <= d <= jusqu_a:
            yield f"{a}-{m:02d}", d
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def prochaine_date(r: MouvementRecurrent, apres: date) -> Optional[date]:
    """Prochaine création prévue (strictement après `apres`)."""
    if not r.actif:
        return None
    depart = max(r.debut, apres)
    for decalage in range(2):  # ce mois-ci ou le suivant
        a, m = depart.year, depart.month + decalage
        if m > 12:
            a, m = a + 1, 1
        d = date_du_mois(a, m, r.jour)
        if d > apres and d >= r.debut:
            return d
    return None


def generer_mouvements_recurrents(db: Session, user: Optional[User] = None) -> List[Mouvement]:
    """Crée les mouvements récurrents dus (d'un utilisateur, ou de tous si user=None)."""
    q = db.query(MouvementRecurrent).filter(MouvementRecurrent.actif.is_(True))
    if user is not None:
        q = q.filter(MouvementRecurrent.user_id == user.id)
    recurrents = q.all()
    if not recurrents:
        return []
    aujourdhui = aujourd_hui()
    deja = set(db.query(GenerationRecurrente.recurrent_id, GenerationRecurrente.periode)
               .filter(GenerationRecurrente.recurrent_id.in_([r.id for r in recurrents])).all())

    crees = []
    for r in recurrents:
        for periode, jour in occurrences(r, aujourdhui):
            if (r.id, periode) in deja:
                continue
            m = Mouvement(user_id=r.user_id, type=r.type, montant=r.montant, libelle=r.libelle, date=jour,
                          categorie_id=r.categorie_id, recurrent_id=r.id, objectif_id=r.objectif_id)
            db.add(m)
            db.flush()
            db.add(GenerationRecurrente(recurrent_id=r.id, periode=periode, mouvement_id=m.id))
            db.refresh(m)
            proprietaire = user or db.get(User, r.user_id)
            journaliser(db, proprietaire, "creation automatique", "mouvement", m.id,
                        apres={**vers_dict(m), "recurrent": r.id})
            try:
                db.commit()
            except IntegrityError:  # déjà créé au même moment par une autre requête
                db.rollback()
                continue
            crees.append(m)
            if m.type == "depense" and m.categorie_id:
                verifier_alerte_budget(db, proprietaire, m.categorie_id, m.date)
    return crees
