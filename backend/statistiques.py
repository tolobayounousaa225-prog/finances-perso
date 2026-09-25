"""
Calculs statistiques partagés par l'API (tableau de bord), les recommandations
et les emails automatiques (bilan mensuel, alertes de budget).
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Budget, Categorie, Mouvement, User
from recommandations import StatsMois


def bornes_mois(annee: int, mois: int):
    return date(annee, mois, 1), date(annee, mois, monthrange(annee, mois)[1])


def mois_precedent(annee: int, mois: int, n: int = 1):
    total = annee * 12 + (mois - 1) - n
    return total // 12, total % 12 + 1


def mouvements_actifs(db: Session, user: User):
    return db.query(Mouvement).filter(Mouvement.user_id == user.id, Mouvement.archive.is_(False))


def depenses_par_categorie(db: Session, user: User, debut: date, fin: date) -> dict:
    rows = (db.query(Categorie.nom, func.sum(Mouvement.montant))
            .join(Categorie, Categorie.id == Mouvement.categorie_id)
            .filter(Mouvement.user_id == user.id, Mouvement.archive.is_(False),
                    Mouvement.type == "depense", Mouvement.date.between(debut, fin))
            .group_by(Categorie.nom).all())
    return {nom: int(total) for nom, total in rows}


def total(db: Session, user: User, type_: str, debut: date, fin: date) -> int:
    val = (mouvements_actifs(db, user).with_entities(func.sum(Mouvement.montant))
           .filter(Mouvement.type == type_, Mouvement.date.between(debut, fin)).scalar())
    return int(val or 0)


def calculer_stats(db: Session, user: User, annee: int, mois: int) -> StatsMois:
    debut, fin = bornes_mois(annee, mois)
    par_categorie = depenses_par_categorie(db, user, debut, fin)
    groupes = {c.nom: c.groupe for c in db.query(Categorie).all()}
    par_groupe = defaultdict(int)
    for cat, montant in par_categorie.items():
        par_groupe[groupes[cat]] += montant

    # Moyennes des 3 mois précédents (pour détecter les hausses)
    cumul, besoins = defaultdict(int), 0
    for n in (1, 2, 3):
        a, m = mois_precedent(annee, mois, n)
        for cat, montant in depenses_par_categorie(db, user, *bornes_mois(a, m)).items():
            cumul[cat] += montant
            if groupes[cat] == "besoin":
                besoins += montant

    epargne_id = db.query(Categorie.id).filter(Categorie.groupe == "epargne")
    epargne_totale = (mouvements_actifs(db, user).with_entities(func.sum(Mouvement.montant))
                      .filter(Mouvement.categorie_id.in_(epargne_id), Mouvement.date <= fin).scalar())

    budgets = {b.categorie.nom: b.montant_mensuel for b in db.query(Budget).filter(Budget.user_id == user.id)}
    return StatsMois(
        revenus=total(db, user, "revenu", debut, fin),
        depenses=sum(par_categorie.values()),
        par_groupe=dict(par_groupe),
        par_categorie=par_categorie,
        budgets=budgets,
        moyenne_3_mois={cat: v / 3 for cat, v in cumul.items()},
        epargne_totale=int(epargne_totale or 0),
        besoins_moyens=besoins / 3,
    )
