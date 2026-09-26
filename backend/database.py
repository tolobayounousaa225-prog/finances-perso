"""
Connexion à la base de données.
SQLite par défaut (un simple fichier), PostgreSQL possible via DATABASE_URL
(ex. Supabase : l'adresse « Connection string » du projet).

DB_SCHEMA (facultatif, PostgreSQL uniquement) : range toutes les tables de l'app dans
un schéma séparé. Permet de partager une base Supabase existante sans mélanger les tables.
"""
import os
import re

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

# POSTGRES_URL : nom utilisé par certaines intégrations (ex. base Neon ajoutée depuis Vercel)
DATABASE_URL = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "sqlite:///./finances.db").strip()
# Certains hébergeurs donnent « postgres://… » : SQLAlchemy attend « postgresql://… »
DATABASE_URL = re.sub(r"^postgres://", "postgresql://", DATABASE_URL)
DB_SCHEMA = os.environ.get("DB_SCHEMA", "").strip()
if DB_SCHEMA and not re.fullmatch(r"[a-z_][a-z0-9_]*", DB_SCHEMA):
    raise ValueError("DB_SCHEMA doit contenir seulement des minuscules, chiffres et _")

EST_SQLITE = DATABASE_URL.startswith("sqlite")
if EST_SQLITE:
    connect_args = {"check_same_thread": False}
else:
    connect_args = {"options": f"-csearch_path={DB_SCHEMA}"} if DB_SCHEMA else {}

# pool_pre_ping : vérifie la connexion avant usage (les bases hébergées coupent les connexions inactives)
# Sur Vercel (serverless), chaque requête peut tourner sur une instance différente : pas de pool.
SUR_VERCEL = bool(os.environ.get("VERCEL"))
options_pool = {"poolclass": NullPool} if SUR_VERCEL and not EST_SQLITE else {"pool_pre_ping": not EST_SQLITE}
engine = create_engine(DATABASE_URL, connect_args=connect_args, **options_pool)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def preparer_schema():
    """Crée le schéma DB_SCHEMA s'il n'existe pas encore (PostgreSQL)."""
    if DB_SCHEMA and not EST_SQLITE:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"'))


def get_db():
    """Dépendance FastAPI : ouvre une session par requête et la referme toujours."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
