"""
Emails automatiques :
  - bilan mensuel (revenus, dépenses, top catégories, recommandations) du mois écoulé ;
  - alerte de budget quand une catégorie atteint 80 % puis 100 % de son plafond.

Chaque email automatique est inscrit dans la table `emails_envoyes` AVANT l'envoi :
la contrainte d'unicité garantit qu'il ne part jamais deux fois, même si le serveur redémarre.
"""
import logging
import os
from datetime import date, datetime
from html import escape
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from emails import envoyer_email
from models import Budget, EmailEnvoye, Mouvement, User
from recommandations import fcfa, generer_recommandations
from statistiques import bornes_mois, calculer_stats, depenses_par_categorie, mois_precedent

log = logging.getLogger("finances.notifications")

FUSEAU = ZoneInfo(os.environ.get("FUSEAU_HORAIRE", "Africa/Abidjan"))
# Adresse de l'app dans les emails : APP_URL si elle est définie, sinon l'adresse de production
# Vercel (variable fournie automatiquement par Vercel), sinon le serveur local.
_VERCEL = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
APP_URL = (os.environ.get("APP_URL") or (f"https://{_VERCEL}" if _VERCEL else "http://localhost:8000")).rstrip("/")


def entete_email() -> str:
    """Bandeau avec le logo (PNG : les messageries n'affichent pas les images SVG)."""
    return (f'<div style="display:flex;align-items:center;gap:10px;margin:0 0 16px">'
            f'<img src="{escape(APP_URL)}/logo-192.png" width="40" height="40" alt="" '
            f'style="border-radius:9px;vertical-align:middle">'
            f'<b style="font-size:18px;color:#0f6e5a;vertical-align:middle">&nbsp;Finances Perso</b></div>')
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]
SEUILS_ALERTE = (100, 80)  # du plus grave au moins grave


def aujourd_hui() -> date:
    return datetime.now(FUSEAU).date()


def reserver(db: Session, user: User, type_: str, cle: str) -> bool:
    """Inscrit l'email comme envoyé. Renvoie False s'il l'avait déjà été."""
    db.add(EmailEnvoye(user_id=user.id, type=type_, cle=cle))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def annuler_reservation(db: Session, user: User, type_: str, cle: str):
    """L'envoi a échoué : on efface la trace pour réessayer plus tard."""
    db.query(EmailEnvoye).filter_by(user_id=user.id, type=type_, cle=cle).delete()
    db.commit()


# ---------------------------------------------------------------------------
# Bilan mensuel
# ---------------------------------------------------------------------------
COULEURS = {"alerte": "#c0392b", "conseil": "#b9770e", "bravo": "#1e8449"}


def contenu_bilan(db: Session, user: User, annee: int, mois: int):
    """Renvoie (sujet, texte brut, html) du bilan d'un mois."""
    s = calculer_stats(db, user, annee, mois)
    recos = generer_recommandations(s)
    solde = s.revenus - s.depenses
    taux = round(s.par_groupe.get("epargne", 0) * 100 / s.revenus) if s.revenus else 0
    top = sorted(s.par_categorie.items(), key=lambda c: -c[1])[:5]
    nom_mois = f"{MOIS_FR[mois - 1]} {annee}"
    sujet = f"Ton bilan de {nom_mois} : solde {'+' if solde >= 0 else ''}{fcfa(solde)}"

    texte = "\n".join([
        f"Bonjour {user.nom},", "", f"Voici ton bilan de {nom_mois}.", "",
        f"Revenus  : {fcfa(s.revenus)}",
        f"Dépenses : {fcfa(s.depenses)}",
        f"Solde    : {fcfa(solde)}",
        f"Épargne  : {taux} % des revenus", "",
        "Principales dépenses :",
        *[f"  - {cat} : {fcfa(m)}" for cat, m in top], "",
        "Recommandations :",
        *[f"  [{r['niveau']}] {r['titre']} — {r['message']}" for r in recos], "",
        f"Détails : {APP_URL}", "",
        "Pour ne plus recevoir ce bilan : onglet Paramètres de l'application.",
    ])

    lignes_top = "".join(
        f'<tr><td style="padding:6px 0">{escape(cat)}</td>'
        f'<td style="padding:6px 0;text-align:right">{fcfa(m)}</td></tr>' for cat, m in top
    ) or '<tr><td style="color:#6b7385">Aucune dépense</td></tr>'
    blocs_recos = "".join(
        f'<div style="border-left:4px solid {COULEURS[r["niveau"]]};background:#f5f6f8;padding:10px 12px;'
        f'margin:0 0 8px;border-radius:6px"><b>{escape(r["titre"])}</b><br>'
        f'<span style="color:#6b7385">{escape(r["message"])}</span></div>' for r in recos
    )
    couleur_solde = COULEURS["bravo"] if solde >= 0 else COULEURS["alerte"]
    html = f"""<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1d2433">
  {entete_email()}
  <h2 style="color:#0f6e5a">Bilan de {nom_mois}</h2>
  <p>Bonjour {escape(user.nom)}, voici le résumé de ton mois.</p>
  <table style="width:100%;border-collapse:collapse;margin:12px 0">
    <tr><td>Revenus</td><td style="text-align:right;color:{COULEURS['bravo']}"><b>{fcfa(s.revenus)}</b></td></tr>
    <tr><td>Dépenses</td><td style="text-align:right;color:{COULEURS['alerte']}"><b>{fcfa(s.depenses)}</b></td></tr>
    <tr><td>Solde</td><td style="text-align:right;color:{couleur_solde}"><b>{fcfa(solde)}</b></td></tr>
    <tr><td>Taux d'épargne</td><td style="text-align:right"><b>{taux} %</b></td></tr>
  </table>
  <h3>Principales dépenses</h3>
  <table style="width:100%;border-collapse:collapse">{lignes_top}</table>
  <h3>Recommandations</h3>
  {blocs_recos}
  <p><a href="{escape(APP_URL)}" style="background:#0f6e5a;color:#fff;padding:10px 16px;border-radius:8px;
     text-decoration:none;display:inline-block">Ouvrir Finances Perso</a></p>
  <p style="color:#6b7385;font-size:12px">Tu reçois cet email car le bilan mensuel est activé dans
     l'onglet Paramètres de l'application.</p>
</div>"""
    return sujet, texte, html


def a_des_mouvements(db: Session, user: User, annee: int, mois: int) -> bool:
    return db.query(Mouvement.id).filter(
        Mouvement.user_id == user.id, Mouvement.archive.is_(False),
        Mouvement.date.between(*bornes_mois(annee, mois))).first() is not None


def envoyer_bilan(db: Session, user: User, annee: int, mois: int) -> bool:
    sujet, texte, html = contenu_bilan(db, user, annee, mois)
    return envoyer_email(user.email, sujet, texte, html)


def envoyer_bilans_du_mois(db: Session, jour: Optional[date] = None) -> int:
    """Tâche planifiée (chaque jour à 8 h) : envoie le bilan du mois écoulé à chaque
    utilisateur qui ne l'a pas encore reçu. Tourner chaque jour, et pas seulement le 1er,
    permet de rattraper un envoi manqué si le serveur était arrêté (jusqu'au 7 du mois).
    Renvoie le nombre de bilans envoyés."""
    jour = jour or aujourd_hui()
    if jour.day > 7:
        return 0
    annee, mois = mois_precedent(jour.year, jour.month)
    cle = f"{annee}-{mois:02d}"
    envoyes = 0
    for user in db.query(User).filter(User.recevoir_bilan.is_(True)).all():
        if not a_des_mouvements(db, user, annee, mois) or not reserver(db, user, "bilan", cle):
            continue
        if envoyer_bilan(db, user, annee, mois):
            envoyes += 1
        else:
            annuler_reservation(db, user, "bilan", cle)
    log.info("Bilans de %s envoyés : %d", cle, envoyes)
    return envoyes


# ---------------------------------------------------------------------------
# Alertes de budget
# ---------------------------------------------------------------------------
def envoyer_bienvenue(user: User) -> bool:
    """Email envoyé juste après l'inscription."""
    sujet = "Bienvenue sur Finances Perso 👋"
    texte = "\n".join([
        f"Bonjour {user.nom},", "",
        "Ton compte Finances Perso est prêt. Pour bien démarrer :",
        "  1. Saisis tes revenus du mois (salaire, primes…).",
        "  2. Ajoute tes dépenses : elles sont classées automatiquement.",
        "  3. Fixe des budgets par catégorie : tu seras alerté à 80 % et à 100 %.", "",
        "Chaque 1er du mois, tu recevras ton bilan avec des conseils personnalisés.", "",
        f"Ouvrir l'application : {APP_URL}",
    ])
    html = f"""<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1d2433">
  {entete_email()}
  <h2 style="color:#0f6e5a">Bienvenue, {escape(user.nom)} !</h2>
  <p>Ton compte est prêt. Pour bien démarrer :</p>
  <ol>
    <li>Saisis tes <b>revenus</b> du mois (salaire, primes…).</li>
    <li>Ajoute tes <b>dépenses</b> : elles sont classées automatiquement.</li>
    <li>Fixe des <b>budgets</b> par catégorie : tu seras alerté à 80 % et à 100 %.</li>
  </ol>
  <p>Chaque 1<sup>er</sup> du mois, tu recevras ton bilan avec des conseils personnalisés.</p>
  <p><a href="{escape(APP_URL)}" style="background:#0f6e5a;color:#fff;padding:10px 16px;border-radius:8px;
     text-decoration:none;display:inline-block">Ouvrir Finances Perso</a></p>
</div>"""
    return envoyer_email(user.email, sujet, texte, html)


def verifier_alerte_budget(db: Session, user: User, categorie_id: int, jour: date) -> Optional[int]:
    """Appelée après chaque dépense. Envoie une alerte si le budget de la catégorie
    vient de franchir 80 % ou 100 % ce mois-ci. Renvoie le seuil alerté (ou None)."""
    if not user.recevoir_alertes:
        return None
    today = aujourd_hui()
    if (jour.year, jour.month) != (today.year, today.month):
        return None  # une dépense saisie pour un ancien mois ne déclenche pas d'alerte
    budget = db.query(Budget).filter_by(user_id=user.id, categorie_id=categorie_id).first()
    if not budget or budget.montant_mensuel <= 0:
        return None

    nom = budget.categorie.nom
    depense = depenses_par_categorie(db, user, *bornes_mois(jour.year, jour.month)).get(nom, 0)
    pourcentage = depense * 100 / budget.montant_mensuel
    seuil = next((s for s in SEUILS_ALERTE if pourcentage >= s), None)
    if seuil is None:
        return None

    base = f"{jour.year}-{jour.month:02d}:{categorie_id}"
    if not reserver(db, user, "alerte", f"{base}:{seuil}"):
        return None  # déjà alerté pour ce seuil ce mois-ci
    if seuil == 100:
        reserver(db, user, "alerte", f"{base}:80")  # inutile d'envoyer ensuite l'alerte 80 %

    if seuil == 100:
        sujet = f"⚠️ Budget « {nom} » dépassé"
        corps = (f"Tu as dépensé {fcfa(depense)} en « {nom} » ce mois-ci, pour un budget de "
                 f"{fcfa(budget.montant_mensuel)} ({pourcentage:.0f} %).")
    else:
        sujet = f"Budget « {nom} » presque atteint ({pourcentage:.0f} %)"
        corps = (f"Tu as déjà utilisé {pourcentage:.0f} % de ton budget « {nom} » : "
                 f"{fcfa(depense)} sur {fcfa(budget.montant_mensuel)}. "
                 f"Il te reste {fcfa(budget.montant_mensuel - depense)} jusqu'à la fin du mois.")
    texte = f"Bonjour {user.nom},\n\n{corps}\n\nDétails : {APP_URL}"
    html = (f'<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1d2433">'
            f'{entete_email()}<h2 style="color:{COULEURS["alerte"] if seuil == 100 else COULEURS["conseil"]}">{escape(sujet)}</h2>'
            f'<p>Bonjour {escape(user.nom)},</p><p>{escape(corps)}</p>'
            f'<p><a href="{escape(APP_URL)}">Ouvrir Finances Perso</a></p></div>')
    if not envoyer_email(user.email, sujet, texte, html):
        annuler_reservation(db, user, "alerte", f"{base}:{seuil}")
        return None
    return seuil
