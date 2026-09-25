"""
Authentification : mots de passe hachés (bcrypt) + jeton JWT valable 7 jours.
Chaque requête protégée récupère l'utilisateur via `get_current_user`.
"""
import os
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import User

SECRET_KEY = os.environ.get("SECRET_KEY", "change-moi-en-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


def hacher(mot_de_passe: str) -> str:
    return pwd_context.hash(mot_de_passe)


def verifier(mot_de_passe: str, hash_: str) -> bool:
    return pwd_context.verify(mot_de_passe, hash_)


def creer_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    erreur = HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée, reconnecte-toi",
                           headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = int(jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["sub"])
    except (JWTError, KeyError, ValueError):
        raise erreur
    user = db.get(User, user_id)
    if not user:
        raise erreur
    return user
