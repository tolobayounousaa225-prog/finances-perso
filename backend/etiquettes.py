"""
Étiquettes : suivre un projet ou un événement (#mariage, #rentree, #voyage-assinie) à travers
plusieurs catégories et plusieurs mois.

Une étiquette s'ajoute dans le champ « Étiquettes » ou directement dans le libellé avec un #
(« Pagne pour la cérémonie #mariage »). Stockage simple : une colonne texte « mariage voyage »
entourée d'espaces (« ␣mariage␣voyage␣ »), pour chercher facilement avec LIKE '% mariage %'.
"""
import re
from typing import Iterable, List, Optional

from categorisation import normaliser

MAX_ETIQUETTES = 10


def normaliser_etiquette(texte: str) -> str:
    """« #Voyage Assinie » -> « voyage-assinie » (minuscules, sans accents ni espaces)."""
    texte = normaliser(texte.lstrip("#"))
    texte = re.sub(r"[^a-z0-9]+", "-", texte).strip("-")
    return texte[:30]


def hashtags(libelle: str) -> List[str]:
    return [normaliser_etiquette(t) for t in re.findall(r"#([^\s#,;]+)", libelle or "")]


def combiner(*sources: Iterable[str]) -> List[str]:
    """Fusionne plusieurs listes d'étiquettes : normalisées, sans doublon, dans l'ordre."""
    resultat = []
    for source in sources:
        for e in source or []:
            e = normaliser_etiquette(e)
            if e and e not in resultat:
                resultat.append(e)
    return resultat[:MAX_ETIQUETTES]


def vers_colonne(etiquettes: List[str]) -> Optional[str]:
    return f" {' '.join(etiquettes)} " if etiquettes else None


def depuis_colonne(valeur: Optional[str]) -> List[str]:
    return (valeur or "").split()


def motif(etiquette: str) -> str:
    """Motif SQL LIKE pour trouver une étiquette dans la colonne."""
    return f"% {normaliser_etiquette(etiquette)} %"
