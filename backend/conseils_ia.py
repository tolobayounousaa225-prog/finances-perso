"""
Conseils rédigés par une IA (Claude, d'Anthropic), en complément des règles de recommandations.py.

Facultatif : sans la variable ANTHROPIC_API_KEY, la fonction est simplement désactivée.
Seuls des chiffres agrégés sont envoyés (totaux par catégorie, budgets, objectifs) :
jamais le nom, l'email ni le détail des mouvements.

Chaque appel est payant (quelques centimes) : les conseils ne sont générés que sur demande
(bouton « Demander des conseils à l'IA ») et gardés en base pour le mois.
Modèle modifiable avec ANTHROPIC_MODEL (par défaut claude-opus-5).
"""
import json
import logging
import os
from typing import Optional

from recommandations import StatsMois

log = logging.getLogger("finances.ia")

MODELE_PAR_DEFAUT = "claude-opus-5"

CONSIGNES = """Tu es un conseiller en finances personnelles pour un utilisateur en Côte d'Ivoire \
(montants en FCFA). À partir des chiffres du mois fournis en JSON, écris 3 à 5 conseils concrets, \
personnalisés et bienveillants, en français, en tutoyant l'utilisateur.

Règles :
- Appuie chaque conseil sur un chiffre précis des données (montant, pourcentage, catégorie).
- Propose des actions réalisables ce mois-ci, adaptées au contexte ivoirien (tontine, Mobile Money, marché…).
- Ne répète pas simplement les alertes déjà listées dans « alertes_regles » : complète-les.
- Si les données sont trop maigres (aucun revenu ou presque aucune dépense), dis-le et explique quoi saisir.
- Pas de conseil d'investissement risqué, pas de produit financier précis.
- Format : une liste à puces en texte simple (lignes commençant par « - »), chaque puce en 1 à 3 phrases, \
sans titre ni conclusion."""


def ia_configuree() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def donnees_pour_ia(s: StatsMois, alertes: list, periode: str) -> dict:
    """Chiffres agrégés uniquement (aucune donnée personnelle)."""
    return {
        "mois": periode,
        "revenus": s.revenus,
        "depenses": s.depenses,
        "solde": s.revenus - s.depenses,
        "depenses_par_groupe_50_30_20": s.par_groupe,
        "depenses_par_categorie": s.par_categorie,
        "moyenne_3_derniers_mois_par_categorie": {c: round(v) for c, v in s.moyenne_3_mois.items()},
        "budgets_mensuels": s.budgets,
        "epargne_totale_cumulee": s.epargne_totale,
        "objectifs_epargne": [{k: o[k] for k in ("nom", "montant_cible", "epargne", "date_limite",
                                                 "par_mois_conseille", "versement_auto", "statut")}
                              for o in s.objectifs],
        "alertes_regles": [a["titre"] for a in alertes],
    }


class ErreurIA(Exception):
    """Message lisible par l'utilisateur."""


def generer_conseils(s: StatsMois, alertes: list, periode: str, client=None) -> tuple[str, str]:
    """Renvoie (texte des conseils, modèle utilisé). Lève ErreurIA en cas de problème.
    `client` : client Anthropic déjà créé (utilisé par les tests)."""
    import anthropic  # importé ici : l'application fonctionne même si la bibliothèque manque

    if not ia_configuree():
        raise ErreurIA("Conseils IA non configurés sur le serveur (variable ANTHROPIC_API_KEY absente).")
    modele = os.environ.get("ANTHROPIC_MODEL", MODELE_PAR_DEFAUT).strip()
    client = client or anthropic.Anthropic(timeout=45.0, max_retries=1)
    donnees = json.dumps(donnees_pour_ia(s, alertes, periode), ensure_ascii=False)
    try:
        reponse = client.beta.messages.create(
            model=modele,
            max_tokens=16000,
            system=CONSIGNES,
            messages=[{"role": "user", "content": f"Voici mes chiffres :\n{donnees}"}],
            output_config={"effort": "low"},  # tâche courte : réponse rapide et moins chère
            # Si le modèle refuse la demande, l'API la relance automatiquement sur un autre modèle
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise ErreurIA("Clé ANTHROPIC_API_KEY invalide : vérifie-la dans les variables d'environnement.")
    except anthropic.PermissionDeniedError:
        raise ErreurIA("La clé Anthropic n'a pas accès à ce modèle (ou le compte n'a plus de crédit).")
    except anthropic.NotFoundError:
        raise ErreurIA(f"Modèle « {modele} » introuvable : vérifie ANTHROPIC_MODEL.")
    except anthropic.RateLimitError:
        raise ErreurIA("Trop de demandes à l'IA pour le moment. Réessaie dans une minute.")
    except anthropic.BadRequestError as e:
        log.warning("Requête refusée par l'API Anthropic : %s", e.message)
        if "credit" in str(e.message).lower():
            raise ErreurIA("Le compte Anthropic n'a plus de crédit.")
        raise ErreurIA("L'IA a refusé la requête (voir les logs du serveur).")
    except anthropic.APIStatusError as e:
        log.warning("Erreur API Anthropic %s : %s", e.status_code, e.message)
        raise ErreurIA("Le service d'IA est indisponible pour le moment. Réessaie plus tard.")
    except anthropic.APIConnectionError:  # comprend les délais dépassés
        raise ErreurIA("Impossible de joindre le service d'IA (réseau ou délai dépassé).")

    if reponse.stop_reason == "refusal":
        raise ErreurIA("L'IA n'a pas pu rédiger de conseils pour ces données.")
    texte = "\n".join(b.text for b in reponse.content if b.type == "text").strip()
    if not texte:
        raise ErreurIA("L'IA n'a renvoyé aucun conseil. Réessaie.")
    return texte, reponse.model


def resume_texte(texte: Optional[str]) -> list:
    """Découpe la liste à puces en conseils séparés (pour l'affichage)."""
    if not texte:
        return []
    lignes = [l.strip() for l in texte.splitlines() if l.strip()]
    conseils, courant = [], ""
    for l in lignes:
        if l.startswith(("- ", "• ", "* ")):
            if courant:
                conseils.append(courant)
            courant = l[2:].strip()
        else:
            courant = f"{courant} {l}".strip()
    if courant:
        conseils.append(courant)
    return conseils

