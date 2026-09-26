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
    recevoir_bilan = Column(Boolean, default=True, nullable=False)    # bilan mensuel par email
    recevoir_alertes = Column(Boolean, default=True, nullable=False)  # alertes de budget par email
    # utilisateur (par défaut) ou superadmin (voit tous les comptes, en lecture seule)
    role = Column(String, default="utilisateur", server_default="utilisateur", nullable=False)
    # Double authentification (voir deux_facteurs.py) : secret actif, secret en cours d'activation,
    # empreintes des codes de secours restants (JSON)
    totp_secret = Column(String, nullable=True)
    totp_en_attente = Column(String, nullable=True)
    codes_secours = Column(Text, nullable=True)
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
    recurrent_id = Column(Integer, ForeignKey("recurrents.id"), nullable=True)  # créé automatiquement
    objectif_id = Column(Integer, ForeignKey("objectifs.id"), nullable=True)    # versement vers un objectif
    etiquettes = Column(String, nullable=True)  # « mariage voyage » entouré d'espaces (voir etiquettes.py)

    categorie = relationship("Categorie")


class MouvementRecurrent(Base):
    """Modèle d'un mouvement qui revient chaque mois (salaire, loyer, abonnement…).
    Le mouvement est créé automatiquement le `jour` du mois (le dernier jour si le mois est plus court)."""
    __tablename__ = "recurrents"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String, nullable=False)  # revenu / depense
    montant = Column(Integer, nullable=False)
    libelle = Column(String, nullable=False)
    categorie_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    objectif_id = Column(Integer, ForeignKey("objectifs.id"), nullable=True)  # épargne automatique
    jour = Column(Integer, nullable=False)  # 1 à 31
    debut = Column(Date, nullable=False)    # aucune création avant cette date
    actif = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    categorie = relationship("Categorie")
    objectif = relationship("ObjectifEpargne")


class GenerationRecurrente(Base):
    """Mémorise chaque mois déjà créé pour un mouvement récurrent : grâce à la contrainte d'unicité,
    il n'est jamais créé deux fois, même si deux requêtes arrivent en même temps
    (et un mouvement archivé n'est pas recréé)."""
    __tablename__ = "generations_recurrentes"
    __table_args__ = (UniqueConstraint("recurrent_id", "periode"),)
    id = Column(Integer, primary_key=True, index=True)
    recurrent_id = Column(Integer, ForeignKey("recurrents.id"), nullable=False, index=True)
    periode = Column(String, nullable=False)  # "2026-09"
    mouvement_id = Column(Integer, ForeignKey("mouvements.id"), nullable=False)


class ObjectifEpargne(Base):
    """Objectif d'épargne (ex. 500 000 FCFA pour un ordinateur d'ici juin).
    La progression est la somme des versements : les mouvements « Épargne » liés à l'objectif."""
    __tablename__ = "objectifs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    nom = Column(String, nullable=False)
    montant_cible = Column(Integer, nullable=False)
    date_limite = Column(Date, nullable=True)
    archive = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConseilIA(Base):
    """Derniers conseils rédigés par l'IA pour un mois (gardés pour ne pas repayer un appel)."""
    __tablename__ = "conseils_ia"
    __table_args__ = (UniqueConstraint("user_id", "periode"),)
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    periode = Column(String, nullable=False)  # "2026-09"
    texte = Column(Text, nullable=False)
    modele = Column(String, nullable=False)
    date = Column(DateTime, default=datetime.utcnow)


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


class EmailEnvoye(Base):
    """Mémorise les emails automatiques déjà envoyés pour ne jamais les envoyer deux fois.
    type : bilan (cle = "2026-09") ou alerte (cle = "2026-09:<categorie_id>:80" ou ":100")."""
    __tablename__ = "emails_envoyes"
    __table_args__ = (UniqueConstraint("user_id", "type", "cle"),)
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String, nullable=False)
    cle = Column(String, nullable=False)
    date = Column(DateTime, default=datetime.utcnow)


class Parametre(Base):
    """Réglages internes de l'application (ex. clé secrète générée au premier démarrage)."""
    __tablename__ = "parametres"
    cle = Column(String, primary_key=True)
    valeur = Column(Text, nullable=False)
