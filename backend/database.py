"""
Connexion à la base de données.
SQLite par défaut (un simple fichier), PostgreSQL possible via DATABASE_URL.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./finances.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dépendance FastAPI : ouvre une session par requête et la referme toujours."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
