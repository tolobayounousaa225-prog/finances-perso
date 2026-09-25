"""
Moteur de recommandations basé sur des règles simples de bonne gestion.

Chaque règle est une petite fonction qui regarde les chiffres du mois et renvoie
0, 1 ou plusieurs conseils. Pour ajouter une règle : écrire une fonction
`regle_xxx(stats)` et l'ajouter à la liste REGLES en bas du fichier.

Plus tard, on pourra envoyer `stats` à une IA (Claude) pour des conseils rédigés.
"""
from dataclasses import dataclass, field
from typing import Dict, List

# Règle 50/30/20 : part idéale du revenu pour chaque groupe de dépenses
CIBLES = {"besoin": 50, "envie": 30, "epargne": 20}


@dataclass
class StatsMois:
    revenus: int
    depenses: int
    par_groupe: Dict[str, int]                # {"besoin": 120000, ...}
    par_categorie: Dict[str, int]             # {"Transport": 30000, ...}
    budgets: Dict[str, int]                   # {"Transport": 25000, ...}
    moyenne_3_mois: Dict[str, float] = field(default_factory=dict)  # dépenses moyennes par catégorie
    epargne_totale: int = 0                   # cumul de tout ce qui a été mis en épargne
    besoins_moyens: float = 0                 # dépenses « besoin » moyennes par mois


def conseil(niveau: str, titre: str, message: str) -> dict:
    """niveau : alerte (rouge), conseil (orange) ou bravo (vert)."""
    return {"niveau": niveau, "titre": titre, "message": message}


def fcfa(montant: float) -> str:
    return f"{round(montant):,} FCFA".replace(",", " ")


def regle_solde_negatif(s: StatsMois) -> List[dict]:
    if s.revenus and s.depenses > s.revenus:
        return [conseil("alerte", "Dépenses supérieures aux revenus",
                        f"Tu as dépensé {fcfa(s.depenses - s.revenus)} de plus que tes revenus ce mois-ci. "
                        "Repère les dépenses « envie » que tu peux reporter.")]
    return []


def regle_50_30_20(s: StatsMois) -> List[dict]:
    if not s.revenus:
        return [conseil("conseil", "Aucun revenu saisi",
                        "Saisis tes revenus du mois pour obtenir une analyse 50/30/20.")]
    res = []
    for groupe, cible in CIBLES.items():
        part = s.par_groupe.get(groupe, 0) * 100 / s.revenus
        if groupe == "epargne":
            if part >= cible:
                res.append(conseil("bravo", "Épargne au top",
                                   f"Tu épargnes {part:.0f} % de tes revenus (objectif {cible} %). Continue !"))
            elif part < 10:
                manque = s.revenus * cible / 100 - s.par_groupe.get(groupe, 0)
                res.append(conseil("conseil", "Épargne trop faible",
                                   f"Tu n'épargnes que {part:.0f} % de tes revenus. Vise {cible} % : "
                                   f"il manque environ {fcfa(manque)} ce mois-ci. Astuce : épargne dès "
                                   "la réception du salaire, pas avec ce qui reste."))
        elif part > cible + 5:
            libelle = "besoins essentiels" if groupe == "besoin" else "envies / loisirs"
            res.append(conseil("conseil", f"Trop de dépenses en {libelle}",
                               f"Les {libelle} représentent {part:.0f} % de tes revenus "
                               f"(recommandé : {cible} % maximum)."))
    return res


def regle_budgets(s: StatsMois) -> List[dict]:
    res = []
    for cat, plafond in s.budgets.items():
        depense = s.par_categorie.get(cat, 0)
        if plafond and depense > plafond:
            res.append(conseil("alerte", f"Budget « {cat} » dépassé",
                               f"{fcfa(depense)} dépensés pour un budget de {fcfa(plafond)} "
                               f"(+{(depense - plafond) * 100 / plafond:.0f} %)."))
        elif plafond and depense >= 0.8 * plafond:
            res.append(conseil("conseil", f"Budget « {cat} » presque atteint",
                               f"{depense * 100 / plafond:.0f} % du budget déjà utilisé."))
    return res


def regle_hausse_categorie(s: StatsMois) -> List[dict]:
    res = []
    for cat, moyenne in s.moyenne_3_mois.items():
        actuel = s.par_categorie.get(cat, 0)
        if moyenne >= 5000 and actuel > 1.3 * moyenne:
            res.append(conseil("conseil", f"Hausse des dépenses « {cat} »",
                               f"{fcfa(actuel)} ce mois-ci contre {fcfa(moyenne)} en moyenne "
                               f"sur les 3 derniers mois (+{(actuel / moyenne - 1) * 100:.0f} %)."))
    return res


def regle_fonds_urgence(s: StatsMois) -> List[dict]:
    if s.besoins_moyens <= 0:
        return []
    objectif = 3 * s.besoins_moyens
    if s.epargne_totale < objectif:
        return [conseil("conseil", "Constitue un fonds d'urgence",
                        f"Un fonds d'urgence couvre 3 mois de dépenses essentielles, soit environ "
                        f"{fcfa(objectif)} pour toi. Tu en as {fcfa(s.epargne_totale)} "
                        f"({s.epargne_totale * 100 / objectif:.0f} %).")]
    return [conseil("bravo", "Fonds d'urgence constitué",
                    "Ton épargne couvre au moins 3 mois de dépenses essentielles.")]


REGLES = [regle_solde_negatif, regle_budgets, regle_50_30_20, regle_hausse_categorie, regle_fonds_urgence]

ORDRE_NIVEAU = {"alerte": 0, "conseil": 1, "bravo": 2}


def generer_recommandations(stats: StatsMois) -> List[dict]:
    resultats = [c for regle in REGLES for c in regle(stats)]
    return sorted(resultats, key=lambda c: ORDRE_NIVEAU[c["niveau"]])
