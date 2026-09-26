"""
Analyses : calendrier des dépenses, habitudes par jour de la semaine,
score de santé financière sur 100 et chiffres utiles aux simulateurs.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models import Mouvement, User
from recommandations import StatsMois, fcfa
from statistiques import agregats, bornes_mois, calculer_stats, categories_par_id, mois_precedent

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


# ---------------------------------------------------------------------------
# Calendrier et jours de la semaine
# ---------------------------------------------------------------------------
def depenses_par_jour(db: Session, user: User, debut: date, fin: date) -> dict:
    lignes = (db.query(Mouvement.date, Mouvement.montant)
              .filter(Mouvement.user_id == user.id, Mouvement.archive.is_(False), Mouvement.type == "depense",
                      Mouvement.date.between(debut, fin)).all())
    totaux = defaultdict(lambda: [0, 0])
    for jour, montant in lignes:
        totaux[jour][0] += montant
        totaux[jour][1] += 1
    return totaux


def calendrier(db: Session, user: User, annee: int, mois: int) -> dict:
    """Dépenses de chaque jour du mois (pour la carte de chaleur) et moyennes par jour de la semaine
    sur les 3 derniers mois (habitudes)."""
    debut, fin = bornes_mois(annee, mois)
    a0, m0 = mois_precedent(annee, mois, 2)
    debut_3_mois = bornes_mois(a0, m0)[0]
    totaux = depenses_par_jour(db, user, debut_3_mois, fin)

    jours = [{"date": (debut + timedelta(days=i)).isoformat(), "jour": i + 1,
              "depenses": totaux[debut + timedelta(days=i)][0], "nb": totaux[debut + timedelta(days=i)][1]}
             for i in range(monthrange(annee, mois)[1])]
    # Moyenne par jour de la semaine : total / nombre de fois où ce jour est passé dans la période
    somme, occurrences = [0] * 7, [0] * 7
    jour = debut_3_mois
    while jour <= fin:
        somme[jour.weekday()] += totaux[jour][0]
        occurrences[jour.weekday()] += 1
        jour += timedelta(days=1)
    semaine = [{"jour": JOURS[i], "moyenne": round(somme[i] / occurrences[i]) if occurrences[i] else 0} for i in range(7)]
    total_mois = sum(j["depenses"] for j in jours)
    avec_depenses = [j for j in jours if j["depenses"]]
    plus_gros = max(avec_depenses, key=lambda j: j["depenses"]) if avec_depenses else None
    jour_fort = max(semaine, key=lambda j: j["moyenne"]) if any(j["moyenne"] for j in semaine) else None
    return {
        "jours": jours,
        "premier_jour_semaine": debut.weekday(),  # 0 = lundi : décalage de la première ligne du calendrier
        "semaine": semaine,
        "total_mois": total_mois,
        "moyenne_par_jour": round(total_mois / len(jours)),
        "jours_sans_depense": len(jours) - len(avec_depenses),
        "plus_gros_jour": plus_gros,
        "jour_le_plus_depensier": jour_fort["jour"] if jour_fort else None,
    }


# ---------------------------------------------------------------------------
# Score de santé financière
# ---------------------------------------------------------------------------
def critere(nom: str, points: float, maximum: int, detail: str, conseil: Optional[str] = None) -> dict:
    return {"nom": nom, "points": round(max(0, min(points, maximum))), "max": maximum, "detail": detail,
            "conseil": conseil}


def score_sante(s: StatsMois) -> dict:
    """Note sur 100 à partir de 5 critères de bonne gestion (épargne, équilibre, budgets,
    fonds d'urgence, envies). None si le mois n'a pas assez de données."""
    if not s.revenus and not s.depenses:
        return {"score": None, "niveau": "Pas assez de données", "criteres": []}
    r = s.revenus
    epargne = s.par_groupe.get("epargne", 0)
    envies = s.par_groupe.get("envie", 0)
    criteres = []

    part = epargne / r if r else 0
    criteres.append(critere("Épargne", part / 0.20 * 25, 25, f"{part * 100:.0f} % des revenus mis de côté (objectif 20 %)",
                            None if part >= 0.20 else f"Épargne {fcfa(r * 0.20 - epargne)} de plus pour atteindre 20 %."))

    if not r:
        criteres.append(critere("Équilibre", 0, 20, "Aucun revenu saisi ce mois-ci", "Saisis tes revenus du mois."))
    elif s.depenses <= r:
        criteres.append(critere("Équilibre", 20, 20, f"Dépenses couvertes par les revenus (solde {fcfa(r - s.depenses)})"))
    else:
        depassement = (s.depenses - r) / r
        criteres.append(critere("Équilibre", 20 * (1 - 2 * depassement), 20,
                                f"Dépenses supérieures aux revenus de {fcfa(s.depenses - r)}",
                                "Reporte des dépenses « envie » pour revenir à l'équilibre."))

    if not s.budgets:
        criteres.append(critere("Budgets", 10, 20, "Aucun budget défini", "Fixe des budgets par catégorie (onglet Budgets)."))
    else:
        respectes = [c for c, plafond in s.budgets.items() if s.par_categorie.get(c, 0) <= plafond]
        depasses = [c for c in s.budgets if c not in respectes]
        criteres.append(critere("Budgets", len(respectes) / len(s.budgets) * 20, 20,
                                f"{len(respectes)} budget(s) respecté(s) sur {len(s.budgets)}",
                                f"Budget dépassé : {', '.join(depasses)}." if depasses else None))

    if s.besoins_moyens <= 0:
        criteres.append(critere("Fonds d'urgence", 10, 20, "Pas encore assez d'historique (3 mois)"))
    else:
        objectif = 3 * s.besoins_moyens
        couverture = s.epargne_totale / objectif
        criteres.append(critere("Fonds d'urgence", couverture * 20, 20,
                                f"{min(couverture, 1) * 100:.0f} % de 3 mois de dépenses essentielles ({fcfa(objectif)})",
                                None if couverture >= 1 else f"Il manque {fcfa(objectif - s.epargne_totale)} d'épargne de précaution."))

    part_envies = envies / r if r else (1 if envies else 0)
    points_envies = 15 if part_envies <= 0.30 else 15 * (0.60 - part_envies) / 0.30
    criteres.append(critere("Envies maîtrisées", points_envies, 15, f"{part_envies * 100:.0f} % des revenus en loisirs et envies (max. conseillé 30 %)",
                            None if part_envies <= 0.30 else "Réduis sorties et achats plaisir pour repasser sous 30 %."))

    score = sum(c["points"] for c in criteres)
    niveau = ("Excellente" if score >= 80 else "Bonne" if score >= 60 else "Fragile" if score >= 40 else "À surveiller")
    return {"score": score, "niveau": niveau, "criteres": criteres}


# ---------------------------------------------------------------------------
# Tout l'onglet Analyses en un seul appel
# ---------------------------------------------------------------------------
def analyses_de(db: Session, user: User, annee: int, mois: int) -> dict:
    # 9 mois d'agrégats : les 6 mois affichés + les 3 mois précédents (moyennes de chacun)
    a0, m0 = mois_precedent(annee, mois, 8)
    lignes = agregats(db, user, bornes_mois(a0, m0)[0], bornes_mois(annee, mois)[1])
    evolution = []
    for n in range(5, -1, -1):
        a, m = mois_precedent(annee, mois, n)
        evolution.append({"mois": f"{a}-{m:02d}", "score": score_sante(calculer_stats(db, user, a, m, lignes))["score"]})
    s = calculer_stats(db, user, annee, mois, lignes)

    # Moyennes des 3 derniers mois (mois choisi compris) : base des simulateurs
    derniers = {mois_precedent(annee, mois, n) for n in range(3)}
    cats = categories_par_id(db)
    moyennes, revenus = defaultdict(int), 0
    for type_, cat_id, a, m, montant in lignes:
        if (a, m) not in derniers:
            continue
        if type_ == "revenu":
            revenus += montant
        elif cat_id in cats:
            moyennes[cats[cat_id][0]] += montant
    return {
        "score": score_sante(s),
        "evolution_score": evolution,
        "calendrier": calendrier(db, user, annee, mois),
        "moyennes_categories": dict(sorted(((c, round(v / 3)) for c, v in moyennes.items()), key=lambda c: -c[1])),
        "revenus_moyens": round(revenus / 3),
    }
