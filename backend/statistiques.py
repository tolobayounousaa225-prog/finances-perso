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


# Les catégories ne changent pas pendant que l'application tourne : on les lit une seule fois.
_categories = None


def categories_par_id(db: Session) -> dict:
    """{id: (nom, groupe)}, mis en cache après la première lecture."""
    global _categories
    if _categories is None:
        _categories = {c.id: (c.nom, c.groupe) for c in db.query(Categorie).all()}
    return _categories


def agregats(db: Session, user: User, debut: date, fin: date) -> list:
    """Une seule requête groupée : totaux par (type, catégorie, année, mois) sur la période.
    Remplace une vingtaine de petites requêtes (chaque aller-retour vers la base coûte du temps)."""
    annee = func.extract("year", Mouvement.date)
    mois = func.extract("month", Mouvement.date)
    rows = (db.query(Mouvement.type, Mouvement.categorie_id, annee, mois, func.sum(Mouvement.montant))
            .filter(Mouvement.user_id == user.id, Mouvement.archive.is_(False), Mouvement.date.between(debut, fin))
            .group_by(Mouvement.type, Mouvement.categorie_id, annee, mois).all())
    return [(t, c, int(a), int(m), int(v or 0)) for t, c, a, m, v in rows]


def fenetre_6_mois(annee: int, mois: int):
    """Du 1er jour d'il y a 5 mois au dernier jour du mois choisi."""
    a0, m0 = mois_precedent(annee, mois, 5)
    return bornes_mois(a0, m0)[0], bornes_mois(annee, mois)[1]


def evolution(lignes: list, annee: int, mois: int) -> list:
    """Revenus et dépenses des 6 derniers mois, à partir des agrégats."""
    resultat = []
    for n in range(5, -1, -1):
        a, m = mois_precedent(annee, mois, n)
        resultat.append({"mois": f"{a}-{m:02d}",
                         "revenus": sum(v for t, _, la, lm, v in lignes if t == "revenu" and (la, lm) == (a, m)),
                         "depenses": sum(v for t, _, la, lm, v in lignes if t == "depense" and (la, lm) == (a, m))})
    return resultat


def calculer_stats(db: Session, user: User, annee: int, mois: int, lignes: list = None) -> StatsMois:
    """Statistiques du mois. `lignes` : agrégats déjà lus (évite de relire la base)."""
    if lignes is None:
        lignes = agregats(db, user, *fenetre_6_mois(annee, mois))
    cats = categories_par_id(db)
    precedents = {mois_precedent(annee, mois, n) for n in (1, 2, 3)}

    par_categorie, par_groupe, cumul = defaultdict(int), defaultdict(int), defaultdict(int)
    revenus, besoins = 0, 0
    for type_, cat_id, a, m, montant in lignes:
        if type_ == "revenu":
            if (a, m) == (annee, mois):
                revenus += montant
            continue
        if cat_id not in cats:
            continue
        nom, groupe = cats[cat_id]
        if (a, m) == (annee, mois):
            par_categorie[nom] += montant
            par_groupe[groupe] += montant
        elif (a, m) in precedents:
            cumul[nom] += montant
            if groupe == "besoin":
                besoins += montant

    fin = bornes_mois(annee, mois)[1]
    ids_epargne = [i for i, (_, groupe) in cats.items() if groupe == "epargne"]
    epargne_totale = (mouvements_actifs(db, user).with_entities(func.sum(Mouvement.montant))
                      .filter(Mouvement.categorie_id.in_(ids_epargne), Mouvement.date <= fin).scalar())
    budgets = {cats[c][0]: v for c, v in db.query(Budget.categorie_id, Budget.montant_mensuel)
               .filter(Budget.user_id == user.id).all() if c in cats}
    return StatsMois(
        revenus=revenus,
        depenses=sum(par_categorie.values()),
        par_groupe=dict(par_groupe),
        par_categorie=dict(par_categorie),
        budgets=budgets,
        moyenne_3_mois={cat: v / 3 for cat, v in cumul.items()},
        epargne_totale=int(epargne_totale or 0),
        besoins_moyens=besoins / 3,
    )
