"""
Envoi d'emails via SMTP.

Sans configuration SMTP (mode développement), les emails ne partent pas :
ils sont affichés dans la console et gardés dans `BOITE_DEV` (utile pour les tests).

Variables d'environnement pour l'envoi réel (ex. avec Gmail : mot de passe d'application) :
  SMTP_HOST=smtp.gmail.com  SMTP_PORT=587  SMTP_USER=...  SMTP_PASSWORD=...
  SMTP_FROM="Finances Perso <moi@gmail.com>"   (facultatif, SMTP_USER par défaut)
"""
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import List

log = logging.getLogger("finances.emails")

BOITE_DEV: List[EmailMessage] = []


def smtp_configure() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def envoyer_email(destinataire: str, sujet: str, texte: str, html: str = "") -> bool:
    """Renvoie True si l'email est parti (ou a été simulé en mode dev), False en cas d'erreur."""
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "finances-perso@localhost")
    msg["To"] = destinataire
    msg.set_content(texte)
    if html:
        msg.add_alternative(html, subtype="html")

    if not smtp_configure():
        BOITE_DEV.append(msg)
        log.info("[email simulé] à %s : %s\n%s", destinataire, sujet, texte)
        return True

    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        if port == 465:  # SSL direct
            serveur = smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, timeout=20)
        else:            # 587 : connexion puis chiffrement STARTTLS
            serveur = smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=20)
            serveur.starttls()
        with serveur:
            if os.environ.get("SMTP_USER"):
                serveur.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            serveur.send_message(msg)
        return True
    except Exception:  # un email raté ne doit jamais faire planter l'application
        log.exception("Échec de l'envoi de l'email à %s", destinataire)
        return False
