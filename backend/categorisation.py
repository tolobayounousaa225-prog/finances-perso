"""
Catégorisation automatique des dépenses par mots-clés.

Ordre de priorité :
  1. les règles apprises de l'utilisateur (ses corrections précédentes) ;
  2. les mots-clés par défaut ci-dessous ;
  3. sinon « Autres ».

Pour enrichir la catégorisation, il suffit d'ajouter des mots dans CATEGORIES_PAR_DEFAUT.
"""
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# nom -> (groupe 50/30/20, icône, mots-clés)
CATEGORIES_PAR_DEFAUT: Dict[str, Tuple[str, str, List[str]]] = {
    "Logement": ("besoin", "🏠", ["loyer", "caution", "bail", "proprietaire", "syndic", "demenagement"]),
    "Alimentation": ("besoin", "🛒", ["marche", "supermarche", "carrefour", "prosuma", "casino", "sococe",
                                     "boulangerie", "pain", "riz", "courses", "attieke", "poisson", "viande"]),
    "Transport": ("besoin", "🚕", ["taxi", "woro", "gbaka", "sotra", "bus", "yango", "uber", "heetch",
                                  "carburant", "essence", "gasoil", "station", "total", "shell", "parking", "peage"]),
    "Factures": ("besoin", "💡", ["cie", "sodeci", "electricite", "eau", "facture", "canal", "internet", "fibre", "wifi"]),
    "Communication": ("besoin", "📱", ["orange", "mtn", "moov", "recharge", "credit", "forfait", "pass internet"]),
    "Santé": ("besoin", "💊", ["pharmacie", "medecin", "clinique", "hopital", "analyse", "ordonnance", "mutuelle"]),
    "Éducation": ("besoin", "🎓", ["scolarite", "ecole", "universite", "inscription", "fournitures", "livre", "formation"]),
    "Famille": ("besoin", "👨‍👩‍👧", ["famille", "parents", "maman", "papa", "envoi", "aide", "funerailles", "bapteme"]),
    "Loisirs": ("envie", "🎉", ["restaurant", "maquis", "bar", "cinema", "sortie", "concert", "netflix",
                              "spotify", "jeu", "voyage", "hotel", "plage"]),
    "Shopping": ("envie", "🛍️", ["vetement", "chaussure", "habit", "boutique", "jumia", "coiffure", "salon", "parfum"]),
    "Épargne": ("epargne", "🏦", ["epargne", "tontine", "placement", "investissement", "livret", "assurance vie"]),
    "Dettes": ("besoin", "💳", ["remboursement", "pret", "credit bancaire", "echeance", "dette"]),
    "Autres": ("envie", "📦", []),
}

CATEGORIE_PAR_DEFAUT = "Autres"


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, espaces simples : « Électricité CIE » -> « electricite cie »."""
    texte = unicodedata.normalize("NFD", texte.lower())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texte).strip()


def _contient(libelle_normalise: str, mot_cle: str) -> bool:
    """Vrai si le mot-clé apparaît comme mot entier (évite « cie » dans « societe »)."""
    return re.search(r"\b" + re.escape(normaliser(mot_cle)) + r"\b", libelle_normalise) is not None


def categoriser(libelle: str, regles_utilisateur: Optional[Dict[str, str]] = None) -> str:
    """Renvoie le nom de la catégorie la plus probable pour un libellé.

    `regles_utilisateur` : {mot_cle: nom_categorie} appris des corrections.
    Les mots-clés les plus longs sont testés en premier (plus précis).
    """
    libelle_n = normaliser(libelle)

    for mot, categorie in sorted((regles_utilisateur or {}).items(), key=lambda r: -len(r[0])):
        if _contient(libelle_n, mot):
            return categorie

    candidats = [(mot, nom) for nom, (_, _, mots) in CATEGORIES_PAR_DEFAUT.items() for mot in mots]
    for mot, nom in sorted(candidats, key=lambda c: -len(c[0])):
        if _contient(libelle_n, mot):
            return nom

    return CATEGORIE_PAR_DEFAUT


def mot_cle_a_apprendre(libelle: str) -> Optional[str]:
    """Choisit le mot à retenir quand l'utilisateur corrige une catégorie :
    le premier mot significatif du libellé (≥ 3 lettres, pas un chiffre)."""
    for mot in normaliser(libelle).split(" "):
        if len(mot) >= 3 and not mot.isdigit() and mot not in MOTS_VIDES:
            return mot
    return None


MOTS_VIDES = {"les", "des", "pour", "avec", "une", "sur", "par", "achat", "paiement", "frais", "mois"}
