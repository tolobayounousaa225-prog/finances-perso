"""
Finances Perso - API FastAPI
Gestion financière personnelle multi-comptes (FCFA) :
revenus, dépenses catégorisées automatiquement, budgets, recommandations, journal de traçabilité.
"""
import csv
import io
import json
import logging
import os
import platform
from contextlib import asynccontextmanager
from datetime import date
from typing import List, Literal, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from auth import cle_secrete, creer_token, get_current_user, hacher, verifier
from categorisation import CATEGORIES_PAR_DEFAUT, categoriser, mot_cle_a_apprendre
from database import DATABASE_URL, Base, SessionLocal, engine, get_db, preparer_schema
from emails import envoyer_email, smtp_configure
from models import Budget, Categorie, JournalAudit, Mouvement, RegleCategorie, User
from notifications import contenu_bilan, envoyer_bilans_du_mois, verifier_alerte_budget
from recommandations import generer_recommandations
from statistiques import bornes_mois, calculer_stats, mois_precedent, total
from taches import demarrer_planificateur

# Affiche dans les logs du serveur les messages de nos modules (emails, tâches planifiées…)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

# ---------------------------------------------------------------------------
# Démarrage : création des tables et des catégories par défaut
# ---------------------------------------------------------------------------
def initialiser_base():
    preparer_schema()
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


class Preferences(BaseModel):
    recevoir_bilan: bool
    recevoir_alertes: bool


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
    return {"id": user.id, "nom": user.nom, "email": user.email, "recevoir_bilan": user.recevoir_bilan,
            "recevoir_alertes": user.recevoir_alertes, "smtp_configure": smtp_configure()}


@app.put("/api/me/preferences")
def modifier_preferences(data: Preferences, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user.recevoir_bilan, user.recevoir_alertes = data.recevoir_bilan, data.recevoir_alertes
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


@app.post("/api/bilan/envoyer")
def envoyer_bilan_maintenant(annee: int, mois: int, user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)):
    """Envoie tout de suite le bilan d'un mois à l'utilisateur (bouton « M'envoyer le bilan »)."""
    sujet, texte, html = contenu_bilan(db, user, annee, mois)
    if not envoyer_email(user.email, sujet, texte, html):
        raise HTTPException(502, "L'email n'a pas pu être envoyé, vérifie la configuration SMTP")
    return {"ok": True, "simule": not smtp_configure()}


@app.get("/api/bilan/apercu", response_class=HTMLResponse)
def apercu_bilan(annee: int, mois: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Le bilan tel qu'il apparaîtra dans l'email."""
    return contenu_bilan(db, user, annee, mois)[2]


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


@app.get("/api/taches/bilans")
def tache_bilans_http(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Déclenche l'envoi des bilans mensuels en attente (appelée chaque jour par Vercel Cron).
    Sans danger si elle est appelée plusieurs fois : un bilan ne part jamais deux fois."""
    secret = os.environ.get("CRON_SECRET")
    if secret and authorization != f"Bearer {secret}":
        raise HTTPException(401, "Non autorisé")
    return {"bilans_envoyes": envoyer_bilans_du_mois(db)}


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
        return {"type": engine.dialect.name, "tables": sorted(inspect(engine).get_table_names())}

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
            "variables_base": [v for v in ("DATABASE_URL", "POSTGRES_URL") if os.environ.get(v)],
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
