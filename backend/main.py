"""
Finances Perso - API FastAPI
Gestion financière personnelle multi-comptes (FCFA) :
revenus, dépenses catégorisées automatiquement, budgets, recommandations, journal de traçabilité.
"""
import csv
import io
import json
import os
from calendar import monthrange
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import creer_token, get_current_user, hacher, verifier
from categorisation import CATEGORIES_PAR_DEFAUT, categoriser, mot_cle_a_apprendre
from database import Base, engine, get_db
from models import Budget, Categorie, JournalAudit, Mouvement, RegleCategorie, User
from recommandations import StatsMois, generer_recommandations

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

# ---------------------------------------------------------------------------
# Démarrage : création des tables et des catégories par défaut
# ---------------------------------------------------------------------------
def initialiser_base():
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        existantes = {c.nom for c in db.query(Categorie).all()}
        for nom, (groupe, icone, _) in CATEGORIES_PAR_DEFAUT.items():
            if nom not in existantes:
                db.add(Categorie(nom=nom, groupe=groupe, icone=icone))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialiser_base()  # exécuté une fois au lancement du serveur
    yield


app = FastAPI(title="Finances Perso", lifespan=lifespan)

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


class CategorieOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nom: str
    groupe: str
    icone: str


class BudgetIn(BaseModel):
    categorie_id: int
    montant_mensuel: int = Field(ge=0)  # 0 = supprimer le budget


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
def vers_dict(m: Mouvement) -> dict:
    return {"type": m.type, "montant": m.montant, "libelle": m.libelle, "date": m.date.isoformat(),
            "categorie": m.categorie.nom if m.categorie else None, "archive": m.archive}


def journaliser(db: Session, user: User, action: str, entite: str, entite_id: int,
                avant: Optional[dict] = None, apres: Optional[dict] = None):
    db.add(JournalAudit(user_id=user.id, action=action, entite=entite, entite_id=entite_id,
                        avant=json.dumps(avant, ensure_ascii=False) if avant else None,
                        apres=json.dumps(apres, ensure_ascii=False) if apres else None))


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


def bornes_mois(annee: int, mois: int):
    return date(annee, mois, 1), date(annee, mois, monthrange(annee, mois)[1])


def mois_precedent(annee: int, mois: int, n: int = 1):
    total = annee * 12 + (mois - 1) - n
    return total // 12, total % 12 + 1


def mouvements_actifs(db: Session, user: User):
    return db.query(Mouvement).filter(Mouvement.user_id == user.id, Mouvement.archive.is_(False))


def depenses_par_categorie(db: Session, user: User, debut: date, fin: date) -> dict:
    rows = (db.query(Categorie.nom, func.sum(Mouvement.montant))
            .join(Categorie, Categorie.id == Mouvement.categorie_id)
            .filter(Mouvement.user_id == user.id, Mouvement.archive.is_(False),
                    Mouvement.type == "depense", Mouvement.date.between(debut, fin))
            .group_by(Categorie.nom).all())
    return {nom: int(total) for nom, total in rows}


def total(db: Session, user: User, type_: str, debut: date, fin: date) -> int:
    val = (mouvements_actifs(db, user).with_entities(func.sum(Mouvement.montant))
           .filter(Mouvement.type == type_, Mouvement.date.between(debut, fin)).scalar())
    return int(val or 0)


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------
@app.post("/api/auth/register")
def inscription(data: Inscription, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "Un compte existe déjà avec cet email")
    user = User(nom=data.nom.strip(), email=data.email, mot_de_passe_hash=hacher(data.mot_de_passe))
    db.add(user)
    db.commit()
    return {"access_token": creer_token(user.id), "token_type": "bearer"}


@app.post("/api/auth/login")
def connexion(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username.strip().lower()).first()
    if not user or not verifier(form.password, user.mot_de_passe_hash):
        raise HTTPException(401, "Email ou mot de passe incorrect")
    return {"access_token": creer_token(user.id), "token_type": "bearer"}


@app.get("/api/me")
def moi(user: User = Depends(get_current_user)):
    return {"id": user.id, "nom": user.nom, "email": user.email}


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
    q = db.query(Mouvement).filter(Mouvement.user_id == user.id)
    if not archives:
        q = q.filter(Mouvement.archive.is_(False))
    if annee and mois:
        q = q.filter(Mouvement.date.between(*bornes_mois(annee, mois)))
    if type:
        q = q.filter(Mouvement.type == type)
    return [mouvement_out(m) for m in q.order_by(Mouvement.date.desc(), Mouvement.id.desc()).all()]


@app.post("/api/mouvements", response_model=MouvementOut, status_code=201)
def creer_mouvement(data: MouvementIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = Mouvement(user_id=user.id, type=data.type, montant=data.montant,
                  libelle=data.libelle.strip(), date=data.date)
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
    return mouvement_out(m)


@app.put("/api/mouvements/{mouvement_id}", response_model=MouvementOut)
def modifier_mouvement(mouvement_id: int, data: MouvementIn, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    m = get_mouvement(db, user, mouvement_id)
    if m.archive:
        raise HTTPException(400, "Un mouvement archivé ne peut plus être modifié")
    avant = vers_dict(m)
    ancienne_categorie = m.categorie_id

    m.type, m.montant, m.libelle, m.date = data.type, data.montant, data.libelle.strip(), data.date
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
    return mouvement_out(m)


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
# Tableau de bord et recommandations
# ---------------------------------------------------------------------------
def calculer_stats(db: Session, user: User, annee: int, mois: int) -> StatsMois:
    debut, fin = bornes_mois(annee, mois)
    par_categorie = depenses_par_categorie(db, user, debut, fin)
    groupes = {c.nom: c.groupe for c in db.query(Categorie).all()}
    par_groupe = defaultdict(int)
    for cat, montant in par_categorie.items():
        par_groupe[groupes[cat]] += montant

    # Moyennes des 3 mois précédents (pour détecter les hausses)
    cumul, besoins = defaultdict(int), 0
    for n in (1, 2, 3):
        a, m = mois_precedent(annee, mois, n)
        for cat, montant in depenses_par_categorie(db, user, *bornes_mois(a, m)).items():
            cumul[cat] += montant
            if groupes[cat] == "besoin":
                besoins += montant

    epargne_id = db.query(Categorie.id).filter(Categorie.groupe == "epargne")
    epargne_totale = (mouvements_actifs(db, user).with_entities(func.sum(Mouvement.montant))
                      .filter(Mouvement.categorie_id.in_(epargne_id), Mouvement.date <= fin).scalar())

    budgets = {b.categorie.nom: b.montant_mensuel for b in db.query(Budget).filter(Budget.user_id == user.id)}
    return StatsMois(
        revenus=total(db, user, "revenu", debut, fin),
        depenses=sum(par_categorie.values()),
        par_groupe=dict(par_groupe),
        par_categorie=par_categorie,
        budgets=budgets,
        moyenne_3_mois={cat: v / 3 for cat, v in cumul.items()},
        epargne_totale=int(epargne_totale or 0),
        besoins_moyens=besoins / 3,
    )


@app.get("/api/dashboard")
def tableau_de_bord(annee: int, mois: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = calculer_stats(db, user, annee, mois)
    evolution = []
    for n in range(5, -1, -1):
        a, m = mois_precedent(annee, mois, n)
        d, f = bornes_mois(a, m)
        evolution.append({"mois": f"{a}-{m:02d}", "revenus": total(db, user, "revenu", d, f),
                          "depenses": total(db, user, "depense", d, f)})
    return {"revenus": s.revenus, "depenses": s.depenses, "solde": s.revenus - s.depenses,
            "par_categorie": s.par_categorie, "par_groupe": s.par_groupe, "budgets": s.budgets,
            "evolution": evolution}


@app.get("/api/recommandations")
def recommandations(annee: int, mois: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return generer_recommandations(calculer_stats(db, user, annee, mois))


# ---------------------------------------------------------------------------
# Traçabilité : journal + export CSV
# ---------------------------------------------------------------------------
@app.get("/api/journal")
def journal(limite: int = 200, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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
    w.writerow(["id", "date", "type", "libelle", "categorie", "montant_fcfa", "archive"])
    for m in db.query(Mouvement).filter(Mouvement.user_id == user.id).order_by(Mouvement.date):
        w.writerow([m.id, m.date.isoformat(), m.type, m.libelle, m.categorie.nom if m.categorie else "",
                    m.montant, "oui" if m.archive else "non"])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=mouvements.csv"})


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
# En local (et si on le souhaite en production), le backend sert aussi le frontend.
# Monté en dernier : les routes /api/... définies plus haut restent prioritaires.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
