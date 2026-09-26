"""
Finances Perso - API FastAPI
Gestion financière personnelle multi-comptes (FCFA) :
revenus, dépenses catégorisées automatiquement, budgets, recommandations, journal de traçabilité.
"""
import csv
import hashlib
import io
import json
import logging
import os
import platform
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import List, Literal, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from auth import cle_secrete, creer_token, get_current_user, hacher, lire_token, verifier
from bilan_pdf import generer_bilan_pdf
from categorisation import CATEGORIES_PAR_DEFAUT, categoriser, mot_cle_a_apprendre
from conseils_ia import ErreurIA, generer_conseils, ia_configuree, resume_texte
import deux_facteurs
from database import DATABASE_URL, Base, SessionLocal, ajouter_colonnes_manquantes, engine, get_db, preparer_schema
from emails import envoyer_email, smtp_configure
from etiquettes import combiner, depuis_colonne, hashtags, motif, normaliser_etiquette, vers_colonne
from models import (Budget, Categorie, ConseilIA, JournalAudit, Mouvement, MouvementRecurrent, ObjectifEpargne, Parametre,
                    RegleCategorie, User)
from notifications import contenu_bilan, envoyer_bienvenue, envoyer_bilans_du_mois, verifier_alerte_budget
from objectifs import decrire, objectifs_de
from recommandations import generer_recommandations
from recurrents import generer_mouvements_recurrents, prochaine_date
from statistiques import agregats, bornes_mois, calculer_stats, evolution, fenetre_6_mois
from taches import demarrer_planificateur
from temps import aujourd_hui
from tracabilite import journaliser, vers_dict

# Affiche dans les logs du serveur les messages de nos modules (emails, tâches planifiées…)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

# ---------------------------------------------------------------------------
# Démarrage : création des tables et des catégories par défaut
# ---------------------------------------------------------------------------
def empreinte_schema() -> str:
    """Résumé de la structure attendue (tables, colonnes, catégories). S'il n'a pas changé depuis
    le dernier démarrage, inutile de tout revérifier : le démarrage à froid est bien plus rapide."""
    description = sorted(f"{t.name}.{c.name}" for t in Base.metadata.sorted_tables for c in t.columns)
    description += sorted(CATEGORIES_PAR_DEFAUT)
    return hashlib.sha256("|".join(description).encode()).hexdigest()


def initialiser_base():
    empreinte = empreinte_schema()
    try:
        with Session(engine) as db:
            deja = db.get(Parametre, "schema_empreinte")
            if deja is not None and deja.valeur == empreinte:
                designer_superadmin(db)
                return
    except Exception:  # première installation : la table parametres n'existe pas encore
        pass
    preparer_schema()
    Base.metadata.create_all(bind=engine)
    ajouter_colonnes_manquantes()
    with Session(engine) as db:
        existantes = {c.nom for c in db.query(Categorie).all()}
        for nom, (groupe, icone, _) in CATEGORIES_PAR_DEFAUT.items():
            if nom not in existantes:
                db.add(Categorie(nom=nom, groupe=groupe, icone=icone))
        db.commit()
        designer_superadmin(db)
        parametre = db.get(Parametre, "schema_empreinte")
        if parametre is None:
            db.add(Parametre(cle="schema_empreinte", valeur=empreinte))
        else:
            parametre.valeur = empreinte
        db.commit()


def designer_superadmin(db: Session):
    """Le super admin est le compte dont l'email est SUPERADMIN_EMAIL ; sans cette variable,
    c'est le premier compte créé. Appelé au démarrage et après chaque inscription."""
    email = os.environ.get("SUPERADMIN_EMAIL", "").strip().lower()
    if email:
        cible = db.query(User).filter(User.email == email).first()
        if cible:  # le compte désigné devient l'unique super admin
            db.query(User).filter(User.role == "superadmin", User.id != cible.id).update({"role": "utilisateur"})
            db.commit()
    elif not db.query(User).filter(User.role == "superadmin").first():
        cible = db.query(User).order_by(User.id).first()
    else:
        cible = None
    if cible and cible.role != "superadmin":
        cible.role = "superadmin"
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialiser_base()  # exécuté une fois au lancement du serveur
    planificateur = demarrer_planificateur()  # bilans mensuels automatiques
    yield
    if planificateur:
        planificateur.shutdown(wait=False)


app = FastAPI(title="Finances Perso", lifespan=lifespan)
log = logging.getLogger("finances.api")


@app.exception_handler(Exception)
async def erreur_inattendue(request: Request, exc: Exception):
    """Toute erreur imprévue est écrite en entier dans les logs du serveur ; l'utilisateur voit
    seulement son type (ex. OperationalError), ce qui aide à diagnostiquer sans rien dévoiler."""
    log.exception("Erreur sur %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"detail": f"Erreur interne du serveur ({type(exc).__name__})"})

# Le frontend peut être hébergé ailleurs (ex. GitHub Pages) : on autorise explicitement son adresse.
# FRONTEND_ORIGINS = liste séparée par des virgules, ex. "https://moi.github.io"
ORIGINES = [o.strip().rstrip("/") for o in os.environ.get("FRONTEND_ORIGINS", "").split(",") if o.strip()]
if ORIGINES:
    app.add_middleware(CORSMiddleware, allow_origins=ORIGINES, allow_methods=["*"],
                       allow_headers=["Authorization", "Content-Type"])


# ---------------------------------------------------------------------------
# Schémas (validation des données reçues / renvoyées)
# ---------------------------------------------------------------------------
class Inscription(BaseModel):
    nom: str = Field(min_length=1)
    email: str
    mot_de_passe: str = Field(min_length=8)

    @field_validator("email")
    @classmethod
    def email_valide(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Email invalide")
        return v


class MouvementIn(BaseModel):
    type: Literal["revenu", "depense"]
    montant: int = Field(gt=0)
    libelle: str = Field(min_length=1)
    date: date
    categorie_id: Optional[int] = None  # vide = catégorisation automatique
    # Étiquettes (#mariage…). Vide (None) lors d'une modification = on garde celles déjà posées.
    # Les #mots du libellé sont ajoutés automatiquement.
    etiquettes: Optional[List[str]] = None


class MouvementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type: str
    montant: int
    libelle: str
    date: date
    categorie_id: Optional[int]
    categorie_nom: Optional[str] = None
    categorie_auto: bool
    archive: bool
    recurrent_id: Optional[int] = None  # créé automatiquement par un mouvement récurrent
    objectif_id: Optional[int] = None   # versement vers un objectif d'épargne
    etiquettes: List[str] = []

    @field_validator("etiquettes", mode="before")
    @classmethod
    def lire_colonne(cls, v):
        return depuis_colonne(v) if isinstance(v, str) or v is None else v


class CategorieOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nom: str
    groupe: str
    icone: str


class Preferences(BaseModel):
    recevoir_bilan: bool
    recevoir_alertes: bool


class BudgetIn(BaseModel):
    categorie_id: int
    montant_mensuel: int = Field(ge=0)  # 0 = supprimer le budget


class RecurrentIn(BaseModel):
    type: Literal["revenu", "depense"]
    montant: int = Field(gt=0)
    libelle: str = Field(min_length=1)
    jour: int = Field(ge=1, le=31)       # jour du mois (31 = dernier jour du mois)
    debut: Optional[date] = None         # vide = aujourd'hui
    categorie_id: Optional[int] = None   # vide = catégorisation automatique
    objectif_id: Optional[int] = None    # versement automatique vers un objectif d'épargne
    actif: bool = True


class ObjectifIn(BaseModel):
    nom: str = Field(min_length=1)
    montant_cible: int = Field(gt=0)
    date_limite: Optional[date] = None


Jour = date  # alias : dans VersementIn, le champ « date » masquerait le type date


class VersementIn(BaseModel):
    montant: int = Field(gt=0)
    date: Optional[Jour] = None  # vide = aujourd'hui


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
def mouvement_out(m: Mouvement) -> MouvementOut:
    out = MouvementOut.model_validate(m)
    out.categorie_nom = m.categorie.nom if m.categorie else None
    return out


def regles_de(db: Session, user: User) -> dict:
    rows = (db.query(RegleCategorie.mot_cle, Categorie.nom)
            .join(Categorie, Categorie.id == RegleCategorie.categorie_id)
            .filter(RegleCategorie.user_id == user.id).all())
    return {mot: nom for mot, nom in rows}


def categorie_auto(db: Session, user: User, libelle: str) -> Categorie:
    nom = categoriser(libelle, regles_de(db, user))
    return db.query(Categorie).filter(Categorie.nom == nom).one()


def get_mouvement(db: Session, user: User, mouvement_id: int) -> Mouvement:
    m = db.get(Mouvement, mouvement_id)
    if not m or m.user_id != user.id:  # un utilisateur ne voit jamais les données d'un autre
        raise HTTPException(404, "Mouvement introuvable")
    return m


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------
@app.post("/api/auth/register")
def inscription(data: Inscription, taches: BackgroundTasks, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "Un compte existe déjà avec cet email")
    user = User(nom=data.nom.strip(), email=data.email, mot_de_passe_hash=hacher(data.mot_de_passe))
    db.add(user)
    db.commit()
    designer_superadmin(db)
    if os.environ.get("VERCEL"):  # serverless : le travail après la réponse n'est pas garanti
        envoyer_bienvenue(user)
    else:
        taches.add_task(envoyer_bienvenue, user)
    return {"access_token": creer_token(user.id), "token_type": "bearer"}


@app.post("/api/auth/login")
def connexion(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username.strip().lower()).first()
    if not user or not verifier(form.password, user.mot_de_passe_hash):
        raise HTTPException(401, "Email ou mot de passe incorrect")
    if user.totp_secret:  # double authentification : le code est demandé à l'étape suivante
        return {"deux_facteurs": True, "jeton_2fa": creer_token(user.id, "2fa", minutes=5)}
    return {"access_token": creer_token(user.id), "token_type": "bearer"}


class Code2FA(BaseModel):
    jeton_2fa: str
    code: str


MAX_ECHECS_2FA = 5  # par tranche de 10 minutes : empêche d'essayer tous les codes


def trop_d_echecs_2fa(db: Session, user: User) -> bool:
    depuis = datetime.utcnow() - timedelta(minutes=10)
    return db.query(func.count(JournalAudit.id)).filter(
        JournalAudit.user_id == user.id, JournalAudit.action == "echec 2fa", JournalAudit.date >= depuis
    ).scalar() >= MAX_ECHECS_2FA


def verifier_code_2fa(db: Session, user: User, code: str) -> bool:
    """Code de l'application d'authentification, ou code de secours (consommé). Les échecs sont journalisés."""
    if deux_facteurs.code_totp_valide(user.totp_secret, code):
        return True
    restantes = deux_facteurs.utiliser_code_secours(user.codes_secours, code)
    if restantes is not None:
        user.codes_secours = restantes
        journaliser(db, user, "code de secours utilise", "securite", user.id,
                    apres={"codes_restants": deux_facteurs.nb_codes_restants(restantes)})
        db.commit()
        return True
    journaliser(db, user, "echec 2fa", "securite", user.id)
    db.commit()
    return False


@app.post("/api/auth/2fa")
def connexion_2fa(data: Code2FA, db: Session = Depends(get_db)):
    """Deuxième étape de la connexion : jeton temporaire (mot de passe déjà vérifié) + code à 6 chiffres."""
    try:
        user = db.get(User, lire_token(data.jeton_2fa, "2fa"))
    except (KeyError, ValueError):
        user = None
    if not user or not user.totp_secret:
        raise HTTPException(401, "Délai dépassé : reconnecte-toi avec ton mot de passe")
    if trop_d_echecs_2fa(db, user):
        raise HTTPException(429, "Trop de codes incorrects. Réessaie dans 10 minutes.")
    if not verifier_code_2fa(db, user, data.code):
        raise HTTPException(401, "Code incorrect")
    return {"access_token": creer_token(user.id), "token_type": "bearer"}


@app.get("/api/me")
def moi(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Appelée à l'ouverture de l'application : on en profite pour créer les mouvements récurrents dus.
    try:
        generer_mouvements_recurrents(db, user)
    except Exception:  # un souci ici ne doit pas empêcher d'ouvrir l'application
        db.rollback()
        log.exception("Erreur pendant la création des mouvements récurrents")
    return {"id": user.id, "nom": user.nom, "email": user.email, "role": user.role, "recevoir_bilan": user.recevoir_bilan,
            "recevoir_alertes": user.recevoir_alertes, "smtp_configure": smtp_configure(),
            "ia_configuree": ia_configuree(), "deux_facteurs": bool(user.totp_secret),
            "codes_secours_restants": deux_facteurs.nb_codes_restants(user.codes_secours) if user.totp_secret else 0}


@app.put("/api/me/preferences")
def modifier_preferences(data: Preferences, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user.recevoir_bilan, user.recevoir_alertes = data.recevoir_bilan, data.recevoir_alertes
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Double authentification (activation / désactivation)
# ---------------------------------------------------------------------------
class CodeSeul(BaseModel):
    code: str


class Desactivation2FA(BaseModel):
    mot_de_passe: str
    code: str


@app.post("/api/2fa/preparer")
def preparer_2fa(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Étape 1 : un nouveau secret, à scanner (QR code) dans l'application d'authentification."""
    if user.totp_secret:
        raise HTTPException(400, "La double authentification est déjà activée")
    user.totp_en_attente = deux_facteurs.nouveau_secret()
    db.commit()
    lien = deux_facteurs.lien_otpauth(user.totp_en_attente, user.email)
    return {"secret": user.totp_en_attente, "lien": lien, "qr_svg": deux_facteurs.qr_code_svg(lien)}


@app.post("/api/2fa/activer")
def activer_2fa(data: CodeSeul, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Étape 2 : le premier code prouve que le téléphone est bien configuré. Renvoie les codes de secours
    (affichés une seule fois)."""
    if user.totp_secret:
        raise HTTPException(400, "La double authentification est déjà activée")
    if not deux_facteurs.code_totp_valide(user.totp_en_attente, data.code):
        raise HTTPException(400, "Code incorrect : vérifie l'heure de ton téléphone et réessaie")
    codes, empreintes = deux_facteurs.nouveaux_codes_secours()
    user.totp_secret, user.totp_en_attente, user.codes_secours = user.totp_en_attente, None, empreintes
    journaliser(db, user, "activation 2fa", "securite", user.id)
    db.commit()
    return {"ok": True, "codes_secours": codes}


@app.post("/api/2fa/desactiver")
def desactiver_2fa(data: Desactivation2FA, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not user.totp_secret:
        raise HTTPException(400, "La double authentification n'est pas activée")
    if not verifier(data.mot_de_passe, user.mot_de_passe_hash):
        raise HTTPException(400, "Mot de passe incorrect")
    if trop_d_echecs_2fa(db, user):
        raise HTTPException(429, "Trop de codes incorrects. Réessaie dans 10 minutes.")
    if not verifier_code_2fa(db, user, data.code):
        raise HTTPException(400, "Code incorrect")
    user.totp_secret = user.totp_en_attente = user.codes_secours = None
    journaliser(db, user, "desactivation 2fa", "securite", user.id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Catégories
# ---------------------------------------------------------------------------
@app.get("/api/categories", response_model=List[CategorieOut])
def liste_categories(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Categorie).order_by(Categorie.nom).all()


@app.get("/api/categoriser")
def suggerer_categorie(libelle: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Aperçu en direct pendant la saisie : quelle catégorie serait choisie ?"""
    return CategorieOut.model_validate(categorie_auto(db, user, libelle))


# ---------------------------------------------------------------------------
# Mouvements (revenus + dépenses)
# ---------------------------------------------------------------------------
@app.get("/api/mouvements", response_model=List[MouvementOut])
def liste_mouvements(annee: Optional[int] = None, mois: Optional[int] = None,
                     type: Optional[Literal["revenu", "depense"]] = None, archives: bool = False,
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return mouvements_de(db, user, annee, mois, type, archives)


def mouvements_de(db: Session, user: User, annee: Optional[int] = None, mois: Optional[int] = None,
                  type: Optional[str] = None, archives: bool = False) -> List[MouvementOut]:
    q = db.query(Mouvement).filter(Mouvement.user_id == user.id)
    if not archives:
        q = q.filter(Mouvement.archive.is_(False))
    if annee and mois:
        q = q.filter(Mouvement.date.between(*bornes_mois(annee, mois)))
    if type:
        q = q.filter(Mouvement.type == type)
    return [mouvement_out(m) for m in q.order_by(Mouvement.date.desc(), Mouvement.id.desc()).all()]


@app.post("/api/mouvements", response_model=MouvementOut, status_code=201)
def creer_mouvement(data: MouvementIn, taches: BackgroundTasks, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    m = Mouvement(user_id=user.id, type=data.type, montant=data.montant,
                  libelle=data.libelle.strip(), date=data.date,
                  etiquettes=vers_colonne(combiner(data.etiquettes, hashtags(data.libelle))))
    if data.type == "depense":
        if data.categorie_id:
            if not db.get(Categorie, data.categorie_id):
                raise HTTPException(400, "Catégorie inconnue")
            m.categorie_id = data.categorie_id
        else:
            m.categorie = categorie_auto(db, user, data.libelle)
            m.categorie_auto = True
    db.add(m)
    db.flush()
    journaliser(db, user, "creation", "mouvement", m.id, apres=vers_dict(m))
    db.commit()
    db.refresh(m)
    planifier_alerte(taches, user, m)
    return mouvement_out(m)


@app.put("/api/mouvements/{mouvement_id}", response_model=MouvementOut)
def modifier_mouvement(mouvement_id: int, data: MouvementIn, taches: BackgroundTasks,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = get_mouvement(db, user, mouvement_id)
    if m.archive:
        raise HTTPException(400, "Un mouvement archivé ne peut plus être modifié")
    avant = vers_dict(m)
    ancienne_categorie = m.categorie_id

    m.type, m.montant, m.libelle, m.date = data.type, data.montant, data.libelle.strip(), data.date
    base = depuis_colonne(m.etiquettes) if data.etiquettes is None else data.etiquettes
    m.etiquettes = vers_colonne(combiner(base, hashtags(m.libelle)))
    if data.type == "revenu":
        m.categorie_id, m.categorie_auto = None, False
    elif data.categorie_id and data.categorie_id != ancienne_categorie:
        if not db.get(Categorie, data.categorie_id):
            raise HTTPException(400, "Catégorie inconnue")
        m.categorie_id, m.categorie_auto = data.categorie_id, False
        apprendre(db, user, m.libelle, data.categorie_id)
    elif not m.categorie_id:
        m.categorie, m.categorie_auto = categorie_auto(db, user, m.libelle), True

    db.flush()
    db.refresh(m)
    journaliser(db, user, "modification", "mouvement", m.id, avant=avant, apres=vers_dict(m))
    db.commit()
    planifier_alerte(taches, user, m)
    return mouvement_out(m)


def planifier_alerte(taches: BackgroundTasks, user: User, m: Mouvement):
    """Vérifie le budget APRÈS avoir répondu, pour que la saisie reste rapide
    même si l'envoi de l'email prend quelques secondes."""
    if m.type == "depense" and m.categorie_id:
        if os.environ.get("VERCEL"):  # serverless : le travail après la réponse n'est pas garanti
            verifier_alerte_en_arriere_plan(user.id, m.categorie_id, m.date)
        else:
            taches.add_task(verifier_alerte_en_arriere_plan, user.id, m.categorie_id, m.date)


def verifier_alerte_en_arriere_plan(user_id: int, categorie_id: int, jour: date):
    with SessionLocal() as db:  # session dédiée : celle de la requête est déjà fermée
        verifier_alerte_budget(db, db.get(User, user_id), categorie_id, jour)


def apprendre(db: Session, user: User, libelle: str, categorie_id: int):
    """L'utilisateur a corrigé la catégorie : on retient un mot-clé pour la prochaine fois."""
    mot = mot_cle_a_apprendre(libelle)
    if not mot:
        return
    regle = db.query(RegleCategorie).filter_by(user_id=user.id, mot_cle=mot).first()
    if regle:
        regle.categorie_id = categorie_id
    else:
        db.add(RegleCategorie(user_id=user.id, mot_cle=mot, categorie_id=categorie_id))


@app.delete("/api/mouvements/{mouvement_id}")
def archiver_mouvement(mouvement_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Pas de suppression réelle : le mouvement est archivé et reste visible dans le journal."""
    m = get_mouvement(db, user, mouvement_id)
    if not m.archive:
        avant = vers_dict(m)
        m.archive = True
        journaliser(db, user, "archivage", "mouvement", m.id, avant=avant, apres=vers_dict(m))
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Recherche et étiquettes
# ---------------------------------------------------------------------------
LIMITE_RESULTATS = 500


@app.get("/api/recherche")
def recherche(q: Optional[str] = None, type: Optional[Literal["revenu", "depense"]] = None,
              categorie_id: Optional[int] = None, etiquette: Optional[str] = None,
              du: Optional[date] = None, au: Optional[date] = None,
              montant_min: Optional[int] = None, montant_max: Optional[int] = None, archives: bool = False,
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Recherche dans tous les mouvements, avec les totaux des résultats
    (par catégorie et par mois) : pratique pour suivre une étiquette ou un fournisseur."""
    requete = db.query(Mouvement).filter(Mouvement.user_id == user.id)
    if not archives:
        requete = requete.filter(Mouvement.archive.is_(False))
    if q and q.strip():
        requete = requete.filter(func.lower(Mouvement.libelle).contains(q.strip().lower()))
    if type:
        requete = requete.filter(Mouvement.type == type)
    if categorie_id:
        requete = requete.filter(Mouvement.categorie_id == categorie_id)
    if etiquette and normaliser_etiquette(etiquette):
        requete = requete.filter(Mouvement.etiquettes.like(motif(etiquette)))
    if du:
        requete = requete.filter(Mouvement.date >= du)
    if au:
        requete = requete.filter(Mouvement.date <= au)
    if montant_min is not None:
        requete = requete.filter(Mouvement.montant >= montant_min)
    if montant_max is not None:
        requete = requete.filter(Mouvement.montant <= montant_max)
    tous = requete.order_by(Mouvement.date.desc(), Mouvement.id.desc()).all()

    actifs = [m for m in tous if not m.archive]
    par_categorie, par_mois = {}, {}
    for m in actifs:
        if m.type == "depense":
            nom = m.categorie.nom if m.categorie else "Autres"
            par_categorie[nom] = par_categorie.get(nom, 0) + m.montant
        mois = m.date.strftime("%Y-%m")
        ligne = par_mois.setdefault(mois, {"mois": mois, "revenus": 0, "depenses": 0})
        ligne["revenus" if m.type == "revenu" else "depenses"] += m.montant
    return {
        "nb": len(tous),
        "total_revenus": sum(m.montant for m in actifs if m.type == "revenu"),
        "total_depenses": sum(m.montant for m in actifs if m.type == "depense"),
        "par_categorie": dict(sorted(par_categorie.items(), key=lambda c: -c[1])),
        "par_mois": sorted(par_mois.values(), key=lambda l: l["mois"]),
        "mouvements": [mouvement_out(m) for m in tous[:LIMITE_RESULTATS]],
        "tronque": len(tous) > LIMITE_RESULTATS,
    }


@app.get("/api/etiquettes")
def liste_etiquettes(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Toutes les étiquettes utilisées, avec leurs totaux (les plus récentes d'abord)."""
    lignes = (db.query(Mouvement.etiquettes, Mouvement.type, Mouvement.montant, Mouvement.date)
              .filter(Mouvement.user_id == user.id, Mouvement.archive.is_(False), Mouvement.etiquettes.isnot(None))
              .all())
    resume = {}
    for etiquettes_, type_, montant, jour in lignes:
        for e in depuis_colonne(etiquettes_):
            r = resume.setdefault(e, {"nom": e, "nb": 0, "depenses": 0, "revenus": 0, "du": jour, "au": jour})
            r["nb"] += 1
            r["depenses" if type_ == "depense" else "revenus"] += montant
            r["du"], r["au"] = min(r["du"], jour), max(r["au"], jour)
    return [{**r, "du": r["du"].isoformat(), "au": r["au"].isoformat()}
            for r in sorted(resume.values(), key=lambda r: r["au"], reverse=True)]


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
@app.get("/api/budgets")
def liste_budgets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [{"categorie_id": b.categorie_id, "categorie_nom": b.categorie.nom, "montant_mensuel": b.montant_mensuel}
            for b in db.query(Budget).filter(Budget.user_id == user.id).all()]


@app.put("/api/budgets")
def definir_budget(data: BudgetIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not db.get(Categorie, data.categorie_id):
        raise HTTPException(400, "Catégorie inconnue")
    b = db.query(Budget).filter_by(user_id=user.id, categorie_id=data.categorie_id).first()
    avant = {"montant_mensuel": b.montant_mensuel} if b else None
    if data.montant_mensuel == 0:
        if b:
            journaliser(db, user, "suppression", "budget", data.categorie_id, avant=avant)
            db.delete(b)
    else:
        if b:
            b.montant_mensuel = data.montant_mensuel
        else:
            db.add(Budget(user_id=user.id, categorie_id=data.categorie_id, montant_mensuel=data.montant_mensuel))
        journaliser(db, user, "modification" if b else "creation", "budget", data.categorie_id,
                    avant=avant, apres={"montant_mensuel": data.montant_mensuel})
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Mouvements récurrents (salaire, loyer, abonnements… créés automatiquement chaque mois)
# ---------------------------------------------------------------------------
def categorie_epargne(db: Session) -> Categorie:
    return db.query(Categorie).filter(Categorie.nom == "Épargne").one()


def get_objectif(db: Session, user: User, objectif_id: int) -> ObjectifEpargne:
    o = db.get(ObjectifEpargne, objectif_id)
    if not o or o.user_id != user.id or o.archive:
        raise HTTPException(404, "Objectif introuvable")
    return o


def get_recurrent(db: Session, user: User, recurrent_id: int) -> MouvementRecurrent:
    r = db.get(MouvementRecurrent, recurrent_id)
    if not r or r.user_id != user.id:
        raise HTTPException(404, "Mouvement récurrent introuvable")
    return r


def recurrent_out(r: MouvementRecurrent) -> dict:
    prochaine = prochaine_date(r, aujourd_hui())
    return {"id": r.id, "type": r.type, "montant": r.montant, "libelle": r.libelle, "jour": r.jour,
            "debut": r.debut.isoformat(), "actif": r.actif, "categorie_id": r.categorie_id,
            "categorie_nom": r.categorie.nom if r.categorie else None, "objectif_id": r.objectif_id,
            "objectif_nom": r.objectif.nom if r.objectif else None,
            "prochaine_date": prochaine.isoformat() if prochaine else None}


def recurrent_vers_dict(r: MouvementRecurrent) -> dict:
    return {k: v for k, v in recurrent_out(r).items() if k not in ("id", "prochaine_date", "categorie_id", "objectif_id")}


def remplir_recurrent(db: Session, user: User, r: MouvementRecurrent, data: RecurrentIn):
    debut = data.debut or aujourd_hui()
    if debut < aujourd_hui() - timedelta(days=366):
        raise HTTPException(400, "La date de début ne peut pas remonter à plus de 12 mois")
    r.type, r.montant, r.libelle, r.jour, r.debut, r.actif = (data.type, data.montant, data.libelle.strip(),
                                                              data.jour, debut, data.actif)
    r.objectif_id = None
    if data.objectif_id:  # versement automatique : toujours une dépense « Épargne »
        r.objectif_id = get_objectif(db, user, data.objectif_id).id
        r.type, r.categorie_id = "depense", categorie_epargne(db).id
    elif data.type == "revenu":
        r.categorie_id = None
    elif data.categorie_id:
        if not db.get(Categorie, data.categorie_id):
            raise HTTPException(400, "Catégorie inconnue")
        r.categorie_id = data.categorie_id
    else:
        r.categorie_id = categorie_auto(db, user, r.libelle).id


@app.get("/api/recurrents")
def liste_recurrents(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rs = (db.query(MouvementRecurrent).filter(MouvementRecurrent.user_id == user.id)
          .order_by(MouvementRecurrent.actif.desc(), MouvementRecurrent.jour, MouvementRecurrent.id).all())
    return [recurrent_out(r) for r in rs]


@app.post("/api/recurrents", status_code=201)
def creer_recurrent(data: RecurrentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = MouvementRecurrent(user_id=user.id)
    remplir_recurrent(db, user, r, data)
    db.add(r)
    db.flush()
    db.refresh(r)
    journaliser(db, user, "creation", "recurrent", r.id, apres=recurrent_vers_dict(r))
    db.commit()
    crees = generer_mouvements_recurrents(db, user)  # les mois déjà dus sont créés tout de suite
    db.refresh(r)
    return {**recurrent_out(r), "mouvements_crees": len(crees)}


@app.put("/api/recurrents/{recurrent_id}")
def modifier_recurrent(recurrent_id: int, data: RecurrentIn, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    r = get_recurrent(db, user, recurrent_id)
    avant = recurrent_vers_dict(r)
    remplir_recurrent(db, user, r, data)
    db.flush()
    db.refresh(r)
    journaliser(db, user, "modification", "recurrent", r.id, avant=avant, apres=recurrent_vers_dict(r))
    db.commit()
    crees = generer_mouvements_recurrents(db, user)
    db.refresh(r)
    return {**recurrent_out(r), "mouvements_crees": len(crees)}


@app.delete("/api/recurrents/{recurrent_id}")
def arreter_recurrent(recurrent_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Arrête les créations futures. Les mouvements déjà créés sont conservés."""
    r = get_recurrent(db, user, recurrent_id)
    if r.actif:
        avant = recurrent_vers_dict(r)
        r.actif = False
        journaliser(db, user, "arret", "recurrent", r.id, avant=avant, apres=recurrent_vers_dict(r))
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Objectifs d'épargne
# ---------------------------------------------------------------------------
def objectif_vers_dict(o: ObjectifEpargne) -> dict:
    return {"nom": o.nom, "montant_cible": o.montant_cible,
            "date_limite": o.date_limite.isoformat() if o.date_limite else None, "archive": o.archive}


def objectif_out(db: Session, user: User, objectif_id: int) -> dict:
    return next(o for o in objectifs_de(db, user, aujourd_hui()) if o["id"] == objectif_id)


@app.get("/api/objectifs")
def liste_objectifs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return objectifs_de(db, user, aujourd_hui())


@app.post("/api/objectifs", status_code=201)
def creer_objectif(data: ObjectifIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    o = ObjectifEpargne(user_id=user.id, nom=data.nom.strip(), montant_cible=data.montant_cible,
                        date_limite=data.date_limite)
    db.add(o)
    db.flush()
    journaliser(db, user, "creation", "objectif", o.id, apres=objectif_vers_dict(o))
    db.commit()
    return decrire(o, 0, aujourd_hui())


@app.put("/api/objectifs/{objectif_id}")
def modifier_objectif(objectif_id: int, data: ObjectifIn, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    o = get_objectif(db, user, objectif_id)
    avant = objectif_vers_dict(o)
    o.nom, o.montant_cible, o.date_limite = data.nom.strip(), data.montant_cible, data.date_limite
    journaliser(db, user, "modification", "objectif", o.id, avant=avant, apres=objectif_vers_dict(o))
    db.commit()
    return objectif_out(db, user, o.id)


@app.delete("/api/objectifs/{objectif_id}")
def archiver_objectif(objectif_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """L'objectif est archivé (les versements restent dans l'épargne) et ses versements récurrents s'arrêtent."""
    o = get_objectif(db, user, objectif_id)
    avant = objectif_vers_dict(o)
    o.archive = True
    journaliser(db, user, "archivage", "objectif", o.id, avant=avant, apres=objectif_vers_dict(o))
    for r in db.query(MouvementRecurrent).filter_by(user_id=user.id, objectif_id=o.id, actif=True):
        avant_r = recurrent_vers_dict(r)
        r.actif = False
        journaliser(db, user, "arret", "recurrent", r.id, avant=avant_r, apres=recurrent_vers_dict(r))
    db.commit()
    return {"ok": True}


@app.post("/api/objectifs/{objectif_id}/versements", status_code=201)
def verser(objectif_id: int, data: VersementIn, taches: BackgroundTasks, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """Met de l'argent de côté pour un objectif : crée une dépense « Épargne » liée à l'objectif."""
    o = get_objectif(db, user, objectif_id)
    m = Mouvement(user_id=user.id, type="depense", montant=data.montant, libelle=f"Épargne : {o.nom}",
                  date=data.date or aujourd_hui(), categorie_id=categorie_epargne(db).id, objectif_id=o.id)
    db.add(m)
    db.flush()
    db.refresh(m)
    journaliser(db, user, "creation", "mouvement", m.id, apres={**vers_dict(m), "objectif": o.nom})
    db.commit()
    planifier_alerte(taches, user, m)
    return objectif_out(db, user, o.id)


# ---------------------------------------------------------------------------
# Tableau de bord et recommandations
# ---------------------------------------------------------------------------
@app.get("/api/dashboard")
def tableau_de_bord(annee: int, mois: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return tableau_de_bord_de(db, user, annee, mois)


def tableau_de_bord_de(db: Session, user: User, annee: int, mois: int) -> dict:
    """Chiffres du mois, évolution sur 6 mois et recommandations, en 3 requêtes seulement."""
    lignes = agregats(db, user, *fenetre_6_mois(annee, mois))
    s = calculer_stats(db, user, annee, mois, lignes)
    return {"revenus": s.revenus, "depenses": s.depenses, "solde": s.revenus - s.depenses,
            "par_categorie": s.par_categorie, "par_groupe": s.par_groupe, "budgets": s.budgets,
            "evolution": evolution(lignes, annee, mois),
            "recommandations": generer_recommandations(s)}


@app.get("/api/recommandations")
def recommandations(annee: int, mois: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return generer_recommandations(calculer_stats(db, user, annee, mois))


@app.post("/api/bilan/envoyer")
def envoyer_bilan_maintenant(annee: int, mois: int, user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)):
    """Envoie tout de suite le bilan d'un mois à l'utilisateur (bouton « M'envoyer le bilan »)."""
    sujet, texte, html = contenu_bilan(db, user, annee, mois)
    if not envoyer_email(user.email, sujet, texte, html):
        raise HTTPException(502, "L'email n'a pas pu être envoyé, vérifie la configuration SMTP")
    return {"ok": True, "simule": not smtp_configure()}


def conseils_ia_du_mois(db: Session, user: User, annee: int, mois: int) -> Optional[ConseilIA]:
    return db.query(ConseilIA).filter_by(user_id=user.id, periode=f"{annee}-{mois:02d}").first()


@app.get("/api/bilan/pdf")
def bilan_pdf(annee: int, mois: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Le bilan du mois en PDF (chiffres, catégories, objectifs, recommandations, mouvements)."""
    s = calculer_stats(db, user, annee, mois)
    conseil = conseils_ia_du_mois(db, user, annee, mois)
    contenu = generer_bilan_pdf(user.nom, annee, mois, s, generer_recommandations(s), mouvements_de(db, user, annee, mois),
                                resume_texte(conseil.texte) if conseil else None)
    return Response(contenu, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=bilan-{annee}-{mois:02d}.pdf"})


# ---------------------------------------------------------------------------
# Conseils rédigés par l'IA (facultatif, voir conseils_ia.py)
# ---------------------------------------------------------------------------
def conseils_ia_out(c: Optional[ConseilIA]) -> dict:
    return {"configuree": ia_configuree(), "conseils": resume_texte(c.texte) if c else [],
            "date": c.date.isoformat(timespec="seconds") if c else None, "modele": c.modele if c else None}


@app.get("/api/conseils-ia")
def lire_conseils_ia(annee: int, mois: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return conseils_ia_out(conseils_ia_du_mois(db, user, annee, mois))


@app.post("/api/conseils-ia")
def demander_conseils_ia(annee: int, mois: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Demande de nouveaux conseils à l'IA. Chaque appel est payant : nombre limité par jour (IA_LIMITE_JOUR)."""
    if not ia_configuree():
        raise HTTPException(400, "Conseils IA non configurés sur le serveur (variable ANTHROPIC_API_KEY absente)")
    limite = int(os.environ.get("IA_LIMITE_JOUR", "5"))
    minuit = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    deja = (db.query(func.count(JournalAudit.id))
            .filter(JournalAudit.user_id == user.id, JournalAudit.entite == "conseils_ia", JournalAudit.date >= minuit)
            .scalar())
    if deja >= limite:
        raise HTTPException(429, f"Limite de {limite} demandes de conseils IA par jour atteinte. Réessaie demain.")
    s = calculer_stats(db, user, annee, mois)
    try:
        texte, modele = generer_conseils(s, generer_recommandations(s), f"{annee}-{mois:02d}")
    except ErreurIA as e:
        raise HTTPException(502, str(e))
    c = conseils_ia_du_mois(db, user, annee, mois)
    if c is None:
        c = ConseilIA(user_id=user.id, periode=f"{annee}-{mois:02d}", texte=texte, modele=modele)
        db.add(c)
    else:
        c.texte, c.modele, c.date = texte, modele, datetime.utcnow()
    db.flush()
    journaliser(db, user, "generation", "conseils_ia", c.id, apres={"mois": c.periode, "modele": modele})
    db.commit()
    return conseils_ia_out(c)


@app.get("/api/bilan/apercu", response_class=HTMLResponse)
def apercu_bilan(annee: int, mois: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Le bilan tel qu'il apparaîtra dans l'email."""
    return contenu_bilan(db, user, annee, mois)[2]


# ---------------------------------------------------------------------------
# Traçabilité : journal + export CSV
# ---------------------------------------------------------------------------
@app.get("/api/journal")
def journal(limite: int = 200, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return journal_de(db, user, limite)


def journal_de(db: Session, user: User, limite: int = 200) -> list:
    rows = (db.query(JournalAudit).filter(JournalAudit.user_id == user.id)
            .order_by(JournalAudit.id.desc()).limit(min(limite, 1000)).all())
    return [{"id": j.id, "date": j.date.isoformat(timespec="seconds"), "action": j.action, "entite": j.entite,
             "entite_id": j.entite_id, "avant": json.loads(j.avant) if j.avant else None,
             "apres": json.loads(j.apres) if j.apres else None} for j in rows]


@app.get("/api/export/csv")
def export_csv(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Tous les mouvements (archives comprises) dans un fichier ouvrable avec Excel."""
    buf = io.StringIO()
    buf.write("﻿")  # BOM : Excel reconnaît les accents
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "date", "type", "libelle", "categorie", "montant_fcfa", "etiquettes", "archive"])
    for m in db.query(Mouvement).filter(Mouvement.user_id == user.id).order_by(Mouvement.date):
        w.writerow([m.id, m.date.isoformat(), m.type, m.libelle, m.categorie.nom if m.categorie else "",
                    m.montant, " ".join(depuis_colonne(m.etiquettes)), "oui" if m.archive else "non"])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=mouvements.csv"})


@app.get("/api/taches/bilans")
def tache_bilans_http(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Déclenche l'envoi des bilans mensuels en attente (appelée chaque jour par Vercel Cron).
    Sans danger si elle est appelée plusieurs fois : un bilan ne part jamais deux fois."""
    secret = os.environ.get("CRON_SECRET")
    if secret and authorization != f"Bearer {secret}":
        raise HTTPException(401, "Non autorisé")
    return {"mouvements_recurrents": len(generer_mouvements_recurrents(db)),
            "bilans_envoyes": envoyer_bilans_du_mois(db)}


# ---------------------------------------------------------------------------
# Super admin : observe tous les comptes, en lecture seule
# ---------------------------------------------------------------------------
def get_superadmin(user: User = Depends(get_current_user)) -> User:
    if user.role != "superadmin":
        raise HTTPException(403, "Réservé au super admin")
    return user


@app.get("/api/admin/utilisateurs")
def admin_liste_utilisateurs(annee: int, mois: int, db: Session = Depends(get_db),
                             admin: User = Depends(get_superadmin)):
    """Tous les comptes avec quelques chiffres clés du mois choisi."""
    debut, fin = bornes_mois(annee, mois)
    totaux = {(uid, t): int(v or 0) for uid, t, v in db.query(Mouvement.user_id, Mouvement.type, func.sum(Mouvement.montant))
              .filter(Mouvement.archive.is_(False), Mouvement.date.between(debut, fin))
              .group_by(Mouvement.user_id, Mouvement.type).all()}
    nb = dict(db.query(Mouvement.user_id, func.count(Mouvement.id))
              .filter(Mouvement.archive.is_(False)).group_by(Mouvement.user_id).all())
    derniere = dict(db.query(JournalAudit.user_id, func.max(JournalAudit.date)).group_by(JournalAudit.user_id).all())
    resultat = []
    for u in db.query(User).order_by(User.id).all():
        resultat.append({
            "id": u.id, "nom": u.nom, "email": u.email, "role": u.role,
            "inscrit_le": u.created_at.isoformat(timespec="seconds") if u.created_at else None,
            "nb_mouvements": nb.get(u.id, 0),
            "revenus_mois": totaux.get((u.id, "revenu"), 0),
            "depenses_mois": totaux.get((u.id, "depense"), 0),
            "derniere_activite": derniere[u.id].isoformat(timespec="seconds") if derniere.get(u.id) else None,
        })
    return resultat


@app.get("/api/admin/utilisateurs/{user_id}")
def admin_detail_utilisateur(user_id: int, annee: int, mois: int, db: Session = Depends(get_db),
                             admin: User = Depends(get_superadmin)):
    """Tableau de bord, mouvements du mois et journal d'un compte. La consultation est
    elle-même inscrite dans le journal du super admin (traçabilité)."""
    cible = db.get(User, user_id)
    if not cible:
        raise HTTPException(404, "Compte introuvable")
    if cible.id != admin.id:
        journaliser(db, admin, "consultation", "utilisateur", cible.id,
                    apres={"compte": cible.email, "mois": f"{annee}-{mois:02d}"})
        db.commit()
    return {
        "utilisateur": {"id": cible.id, "nom": cible.nom, "email": cible.email, "role": cible.role},
        "tableau_de_bord": tableau_de_bord_de(db, cible, annee, mois),
        "mouvements": mouvements_de(db, cible, annee, mois),
        "journal": journal_de(db, cible, 50),
    }


def _masquer(message: str) -> str:
    """Retire le mot de passe de la base d'un message d'erreur avant de l'afficher."""
    try:
        mdp = make_url(DATABASE_URL).password
    except Exception:
        mdp = None
    if mdp:
        message = message.replace(str(mdp), "***")
    return message[:300]


@app.get("/api/diagnostic")
def diagnostic():
    """Vérifie chaque maillon (base, écriture, mot de passe, clé, jeton) et dit lequel bloque.
    Ne renvoie aucune donnée d'utilisateur ni aucun secret."""
    resultats = {}

    def verifier_etape(nom, fonction):
        try:
            resultats[nom] = {"ok": True, **(fonction() or {})}
        except Exception as e:  # on veut justement voir l'erreur
            resultats[nom] = {"ok": False, "erreur": _masquer(f"{type(e).__name__}: {e}")}

    def base():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            debut = time.perf_counter()
            for _ in range(3):
                conn.execute(text("SELECT 1"))
            latence = (time.perf_counter() - debut) / 3 * 1000
        hote = make_url(DATABASE_URL).host or ""
        region = re.search(r"\.([a-z]{2}-[a-z]+-\d)\.", hote)
        return {"type": engine.dialect.name, "tables": sorted(inspect(engine).get_table_names()),
                "aller_retour_ms": round(latence, 1),
                "region_base": region.group(1) if region else None}

    def ecriture():
        with engine.connect() as conn:
            trans = conn.begin()
            conn.execute(text("INSERT INTO parametres (cle, valeur) VALUES ('diagnostic', 'test')"))
            trans.rollback()  # rien n'est gardé

    verifier_etape("base_de_donnees", base)
    verifier_etape("ecriture", ecriture)
    verifier_etape("hachage_mot_de_passe", lambda: {"longueur": len(hacher("motdepasse1"))})
    verifier_etape("cle_secrete", lambda: {"source": "variable SECRET_KEY" if os.environ.get("SECRET_KEY")
                                           else "générée et gardée en base", "longueur": len(cle_secrete())})
    verifier_etape("jeton", lambda: {"longueur": len(creer_token(0))})
    return {
        "tout_va_bien": all(r["ok"] for r in resultats.values()),
        "etapes": resultats,
        "environnement": {
            "python": platform.python_version(),
            "vercel": bool(os.environ.get("VERCEL")),
            "region_serveur": os.environ.get("VERCEL_REGION"),
            "variables_base": [v for v in ("DATABASE_URL", "POSTGRES_URL") if os.environ.get(v)],
        },
        # Noms des variables d'email que l'application voit (jamais leurs valeurs)
        "emails": {
            "envoi_reel": smtp_configure(),
            "variables_presentes": [v for v in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
                                    if os.environ.get(v)],
            "variables_proches": sorted(v for v in os.environ
                                        if "SMTP" in v.upper() and v not in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER",
                                                                             "SMTP_PASSWORD", "SMTP_FROM")),
        },
    }


@app.get("/api/health")
@app.get("/api/index", include_in_schema=False)  # vérifications automatiques de Vercel
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
# En local (et si on le souhaite en production), le backend sert aussi le frontend.
# Monté en dernier : les routes /api/... définies plus haut restent prioritaires.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
