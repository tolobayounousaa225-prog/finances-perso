"""
Double authentification (2FA) par code à 6 chiffres (TOTP), compatible avec
Google Authenticator, Microsoft Authenticator, Authy…

Activation : l'utilisateur scanne un QR code, puis confirme avec un premier code.
Il reçoit 8 codes de secours à usage unique (si le téléphone est perdu) ; seuls
leurs empreintes (SHA-256) sont gardées en base.
"""
import hashlib
import json
import secrets
from typing import List, Optional, Tuple

import pyotp
import qrcode
import qrcode.image.svg

EMETTEUR = "Finances Perso"
NB_CODES_SECOURS = 8


def nouveau_secret() -> str:
    return pyotp.random_base32()


def lien_otpauth(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=EMETTEUR)


def qr_code_svg(texte: str) -> str:
    """QR code au format SVG (image vectorielle, affichable directement dans la page)."""
    image = qrcode.make(texte, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return image.to_string(encoding="unicode")


def nettoyer(code: str) -> str:
    return "".join(c for c in (code or "") if c.isalnum()).upper()


def code_totp_valide(secret: Optional[str], code: str) -> bool:
    """Accepte aussi le code précédent ou suivant (30 s d'écart) : horloge du téléphone imprécise."""
    code = nettoyer(code)
    return bool(secret) and len(code) == 6 and code.isdigit() and pyotp.TOTP(secret).verify(code, valid_window=1)


def empreinte(code: str) -> str:
    return hashlib.sha256(nettoyer(code).encode()).hexdigest()


def nouveaux_codes_secours() -> Tuple[List[str], str]:
    """Renvoie (codes à montrer une seule fois, empreintes à stocker en JSON)."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}".upper() for _ in range(NB_CODES_SECOURS)]
    return codes, json.dumps([empreinte(c) for c in codes])


def utiliser_code_secours(empreintes_json: Optional[str], code: str) -> Optional[str]:
    """Si le code de secours est valide, renvoie les empreintes restantes (le code est consommé).
    Sinon renvoie None."""
    restantes = json.loads(empreintes_json or "[]")
    e = empreinte(code)
    if len(nettoyer(code)) != 8 or e not in restantes:
        return None
    restantes.remove(e)
    return json.dumps(restantes)


def nb_codes_restants(empreintes_json: Optional[str]) -> int:
    return len(json.loads(empreintes_json or "[]"))
