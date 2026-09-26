"""
Authentification : mots de passe hachés (bcrypt) + jeton JWT valable 7 jours.
Chaque requête protégée récupère l'utilisateur via `get_current_user`.
"""
import os
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from models import Parametre, User

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


def hacher(mot_de_passe: str) -> str:
    return pwd_context.hash(mot_de_passe)


def verifier(mot_de_passe: str, hash_: str) -> bool:
    return pwd_context.verify(mot_de_passe, hash_)


_cle_en_cache = None


def cle_secrete() -> str:
    """Clé qui signe les jetons de connexion. SECRET_KEY si elle est définie ; sinon une clé
    aléatoire est créée au premier démarrage et gardée en base (aucun réglage à faire)."""
    global _cle_en_cache
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    if _cle_en_cache is None:
        with SessionLocal() as db:
            p = db.get(Parametre, "secret_key")
            if p is None:
                db.add(Parametre(cle="secret_key", valeur=secrets.token_hex(32)))
                try:
                    db.commit()
                except IntegrityError:  # créée au même moment par une autre instance
                    db.rollback()
                p = db.get(Parametre, "secret_key")
            _cle_en_cache = p.valeur
    return _cle_en_cache


def creer_token(user_id: int, type_: str = "acces", minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    """type_ « acces » : jeton de connexion ; « 2fa » : jeton temporaire (5 min) qui prouve seulement
    que le mot de passe est bon, en attendant le code de double authentification."""
    expire = datetime.utcnow() + timedelta(minutes=minutes)
    return jwt.encode({"sub": str(user_id), "exp": expire, "typ": type_}, cle_secrete(), algorithm=ALGORITHM)


def lire_token(token: str, type_: str) -> int:
    """Renvoie l'id de l'utilisateur, ou lève ValueError si le jeton est invalide, expiré ou d'un autre type."""
    try:
        contenu = jwt.decode(token, cle_secrete(), algorithms=[ALGORITHM])
    except JWTError:
        raise ValueError("jeton invalide")
    if contenu.get("typ", "acces") != type_:  # anciens jetons sans « typ » : jetons de connexion
        raise ValueError("mauvais type de jeton")
    return int(contenu["sub"])


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    erreur = HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée, reconnecte-toi",
                           headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = lire_token(token, "acces")
    except (KeyError, ValueError):
        raise erreur
    user = db.get(User, user_id)
    if not user:
        raise erreur
    return user
