"""
Modèles de données (tables SQL).

Principe de traçabilité : on ne supprime jamais un mouvement, on l'archive,
et chaque création / modification / archivage est inscrit dans le journal.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    mot_de_passe_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Categorie(Base):
    """Catégorie de dépense. `groupe` sert à la règle 50/30/20 :
    besoin (logement, alimentation…), envie (loisirs…), epargne."""
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String, unique=True, nullable=False)
    groupe = Column(String, nullable=False)  # besoin / envie / epargne
    icone = Column(String, default="•")


class Mouvement(Base):
    """Un revenu ou une dépense. Montants en FCFA entiers (pas de centimes)."""
    __tablename__ = "mouvements"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String, nullable=False)  # revenu / depense
    montant = Column(Integer, nullable=False)
    libelle = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    categorie_id = Column(Integer, ForeignKey("categories.id"), nullable=True)  # vide pour un revenu
    categorie_auto = Column(Boolean, default=False)  # True si classée automatiquement
    archive = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    categorie = relationship("Categorie")


class RegleCategorie(Base):
    """Mot-clé appris d'une correction de l'utilisateur : « canal+ » -> Loisirs.
    Prioritaire sur les mots-clés par défaut."""
    __tablename__ = "regles_categorie"
    __table_args__ = (UniqueConstraint("user_id", "mot_cle"),)
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    mot_cle = Column(String, nullable=False)
    categorie_id = Column(Integer, ForeignKey("categories.id"), nullable=False)


class Budget(Base):
    """Plafond mensuel qu'un utilisateur se fixe pour une catégorie."""
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id", "categorie_id"),)
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    categorie_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    montant_mensuel = Column(Integer, nullable=False)

    categorie = relationship("Categorie")


class JournalAudit(Base):
    """Trace de chaque action : qui, quand, quoi, valeur avant / après (JSON)."""
    __tablename__ = "journal"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String, nullable=False)  # creation / modification / archivage
    entite = Column(String, nullable=False)  # mouvement / budget
    entite_id = Column(Integer, nullable=False)
    avant = Column(Text, nullable=True)
    apres = Column(Text, nullable=True)
    date = Column(DateTime, default=datetime.utcnow, index=True)
