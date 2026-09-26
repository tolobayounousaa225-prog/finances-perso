"""
Tests automatiques. Lancer avec :  pytest
Chaque test utilise une base SQLite neuve dans un dossier temporaire.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("ACTIVER_TACHES", "false")
    monkeypatch.delenv("SMTP_HOST", raising=False)  # emails simulés
    for mod in ["main", "database", "models", "auth", "statistiques", "notifications", "taches", "emails"]:
        sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        yield c


def inscrire(client, email="awa@test.ci"):
    r = client.post("/api/auth/register", json={"nom": "Awa", "email": email, "mot_de_passe": "motdepasse1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def ajouter(client, h, type_, montant, libelle, d="2026-09-10", **extra):
    r = client.post("/api/mouvements", headers=h,
                    json={"type": type_, "montant": montant, "libelle": libelle, "date": d, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_categorisation_automatique():
    from categorisation import categoriser
    assert categoriser("Recharge Orange 5000") == "Communication"
    assert categoriser("Facture CIE septembre") == "Factures"
    assert categoriser("Électricité") == "Factures"  # accents ignorés
    assert categoriser("Société générale") == "Autres"  # « cie » n'est pas trouvé dans « societe »
    assert categoriser("Remboursement crédit bancaire") == "Dettes"  # mot-clé le plus long gagne
    assert categoriser("Canal+ abonnement", {"canal": "Loisirs"}) == "Loisirs"  # règle apprise prioritaire


def test_connexion(client):
    inscrire(client)
    r = client.post("/api/auth/login", data={"username": "AWA@test.ci", "password": "motdepasse1"})
    assert r.status_code == 200
    assert client.post("/api/auth/login", data={"username": "awa@test.ci", "password": "faux"}).status_code == 401
    assert client.get("/api/me").status_code == 401


def test_depense_categorisee_et_apprentissage(client):
    h = inscrire(client)
    m = ajouter(client, h, "depense", 3000, "Yango vers Plateau")
    assert m["categorie_nom"] == "Transport" and m["categorie_auto"]

    # L'utilisateur corrige « Kiosque » (Autres) en Alimentation -> l'app retient « kiosque »
    m = ajouter(client, h, "depense", 1500, "Kiosque Adjamé")
    assert m["categorie_nom"] == "Autres"
    alim = next(c for c in client.get("/api/categories", headers=h).json() if c["nom"] == "Alimentation")
    client.put(f"/api/mouvements/{m['id']}", headers=h,
               json={**{k: m[k] for k in ("type", "montant", "libelle", "date")}, "categorie_id": alim["id"]})
    assert ajouter(client, h, "depense", 800, "kiosque Cocody")["categorie_nom"] == "Alimentation"


def test_archivage_et_journal(client):
    h = inscrire(client)
    m = ajouter(client, h, "revenu", 300000, "Salaire")
    client.delete(f"/api/mouvements/{m['id']}", headers=h)
    assert client.get("/api/mouvements", headers=h).json() == []
    assert len(client.get("/api/mouvements?archives=true", headers=h).json()) == 1
    actions = [j["action"] for j in client.get("/api/journal", headers=h).json()]
    assert actions == ["archivage", "creation"]


def test_isolation_entre_comptes(client):
    h1, h2 = inscrire(client, "a@test.ci"), inscrire(client, "b@test.ci")
    m = ajouter(client, h1, "revenu", 100000, "Salaire")
    assert client.get("/api/mouvements", headers=h2).json() == []
    assert client.delete(f"/api/mouvements/{m['id']}", headers=h2).status_code == 404


def test_dashboard_et_recommandations(client):
    h = inscrire(client)
    ajouter(client, h, "revenu", 200000, "Salaire")
    ajouter(client, h, "depense", 150000, "Loyer")
    ajouter(client, h, "depense", 80000, "Restaurant anniversaire")
    client.put("/api/budgets", headers=h, json={"categorie_id": 1, "montant_mensuel": 1000})

    d = client.get("/api/dashboard?annee=2026&mois=9", headers=h).json()
    assert d["solde"] == -30000
    assert d["par_categorie"] == {"Logement": 150000, "Loisirs": 80000}

    recos = client.get("/api/recommandations?annee=2026&mois=9", headers=h).json()
    titres = [r["titre"] for r in recos]
    assert "Dépenses supérieures aux revenus" in titres
    assert "Épargne trop faible" in titres
    assert recos[0]["niveau"] == "alerte"  # les alertes d'abord


def test_export_csv(client):
    h = inscrire(client)
    ajouter(client, h, "depense", 2000, "Pharmacie")
    r = client.get("/api/export/csv", headers=h)
    assert "Pharmacie" in r.text and "Santé" in r.text


# ---------------------------------------------------------------------------
# Emails automatiques : bilan mensuel et alertes de budget
# ---------------------------------------------------------------------------
def boite():
    from emails import BOITE_DEV
    return BOITE_DEV


def test_bilan_mensuel_envoye_une_seule_fois(client):
    from datetime import date
    from database import SessionLocal
    from notifications import envoyer_bilans_du_mois

    h = inscrire(client)
    inscrire(client, "sans-mouvement@test.ci")  # rien saisi en août : pas de bilan vide
    ajouter(client, h, "revenu", 300000, "Salaire", d="2026-08-05")
    ajouter(client, h, "depense", 100000, "Loyer", d="2026-08-06")

    with SessionLocal() as db:
        assert envoyer_bilans_du_mois(db, jour=date(2026, 9, 1)) == 1
        assert envoyer_bilans_du_mois(db, jour=date(2026, 9, 2)) == 0  # déjà reçu
        assert envoyer_bilans_du_mois(db, jour=date(2026, 9, 15)) == 0  # hors période de rattrapage

    assert len(boite()) == 1
    mail = boite()[0]
    assert mail["To"] == "awa@test.ci"
    assert "août 2026" in mail["Subject"] and "200 000 FCFA" in mail["Subject"]
    assert "Logement : 100 000 FCFA" in mail.get_body(("plain",)).get_content()


def test_bilan_desactive(client):
    from datetime import date
    from database import SessionLocal
    from notifications import envoyer_bilans_du_mois

    h = inscrire(client)
    ajouter(client, h, "revenu", 300000, "Salaire", d="2026-08-05")
    client.put("/api/me/preferences", headers=h, json={"recevoir_bilan": False, "recevoir_alertes": True})
    assert client.get("/api/me", headers=h).json()["recevoir_bilan"] is False
    with SessionLocal() as db:
        assert envoyer_bilans_du_mois(db, jour=date(2026, 9, 1)) == 0


def test_envoi_manuel_et_apercu(client):
    h = inscrire(client)
    ajouter(client, h, "revenu", 50000, "Prime", d="2026-09-01")
    assert client.post("/api/bilan/envoyer?annee=2026&mois=9", headers=h).json() == {"ok": True, "simule": True}
    assert "septembre 2026" in boite()[-1]["Subject"]
    assert "Bilan de septembre 2026" in client.get("/api/bilan/apercu?annee=2026&mois=9", headers=h).text


def test_alertes_budget_80_puis_100(client, monkeypatch):
    from datetime import date
    import notifications
    monkeypatch.setattr(notifications, "aujourd_hui", lambda: date(2026, 9, 20))

    h = inscrire(client)
    transport = next(c for c in client.get("/api/categories", headers=h).json() if c["nom"] == "Transport")
    client.put("/api/budgets", headers=h, json={"categorie_id": transport["id"], "montant_mensuel": 10000})

    ajouter(client, h, "depense", 5000, "Taxi", d="2026-09-10")        # 50 % : rien
    assert boite() == []
    ajouter(client, h, "depense", 3500, "Yango", d="2026-09-11")       # 85 % : alerte 80 %
    assert len(boite()) == 1 and "presque atteint" in boite()[0]["Subject"]
    ajouter(client, h, "depense", 500, "Gbaka", d="2026-09-12")        # 90 % : déjà alerté
    assert len(boite()) == 1
    ajouter(client, h, "depense", 2000, "Taxi retour", d="2026-09-13")  # 110 % : alerte 100 %
    assert len(boite()) == 2 and "dépassé" in boite()[1]["Subject"]
    ajouter(client, h, "depense", 1000, "Taxi", d="2026-09-14")        # toujours dépassé : pas de spam
    ajouter(client, h, "depense", 90000, "Taxi", d="2026-08-14")       # ancien mois : pas d'alerte
    assert len(boite()) == 2


def test_alertes_desactivees(client, monkeypatch):
    from datetime import date
    import notifications
    monkeypatch.setattr(notifications, "aujourd_hui", lambda: date(2026, 9, 20))

    h = inscrire(client)
    client.put("/api/me/preferences", headers=h, json={"recevoir_bilan": True, "recevoir_alertes": False})
    client.put("/api/budgets", headers=h, json={"categorie_id": 1, "montant_mensuel": 1000})
    ajouter(client, h, "depense", 5000, "Divers", d="2026-09-10", categorie_id=1)
    assert boite() == []


def test_planificateur(client, monkeypatch):
    monkeypatch.setenv("ACTIVER_TACHES", "true")
    import taches
    monkeypatch.setattr(taches, "tache_bilans", lambda: None)
    p = taches.demarrer_planificateur()
    try:
        job = p.get_job("bilans")
        assert "hour='8'" in str(job.trigger) and "minute='0'" in str(job.trigger)
    finally:
        p.shutdown(wait=False)


def test_cors_frontend_github_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/cors.db")
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://moi.github.io/")
    for mod in ["main", "database", "models", "auth"]:
        sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        preflight = {"Origin": "https://moi.github.io", "Access-Control-Request-Method": "GET",
                     "Access-Control-Request-Headers": "authorization"}
        r = c.options("/api/categories", headers=preflight)
        assert r.headers["access-control-allow-origin"] == "https://moi.github.io"
        r = c.options("/api/categories", headers={**preflight, "Origin": "https://pirate.example"})
        assert "access-control-allow-origin" not in r.headers


def test_frontend_servi_en_local(client):
    assert "Finances Perso" in client.get("/").text
    assert 'window.API_URL = ""' in client.get("/config.js").text
    assert client.get("/vendor/chart.umd.min.js").status_code == 200


# ---------------------------------------------------------------------------
# Hébergement Vercel
# ---------------------------------------------------------------------------
def test_cle_secrete_generee_et_gardee_en_base(client, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    import auth
    auth._cle_en_cache = None
    h = inscrire(client)
    cle = auth.cle_secrete()
    assert len(cle) == 64
    auth._cle_en_cache = None  # simule un redémarrage : la clé est relue en base
    assert auth.cle_secrete() == cle
    assert client.get("/api/me", headers=h).status_code == 200  # le jeton reste valide


def test_tache_bilans_http(client, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get("/api/taches/bilans").json() == {"bilans_envoyes": 0}
    monkeypatch.setenv("CRON_SECRET", "abc")
    assert client.get("/api/taches/bilans").status_code == 401
    r = client.get("/api/taches/bilans", headers={"Authorization": "Bearer abc"})
    assert r.status_code == 200


def test_point_entree_vercel(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/vercel.db")
    monkeypatch.setenv("VERCEL", "1")
    for mod in ["main", "database", "models", "auth", "statistiques", "notifications", "taches", "emails", "index"]:
        sys.modules.pop(mod, None)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
    import taches
    assert taches.demarrer_planificateur() is None  # pas de planificateur sur Vercel
    import index  # api/index.py : crée les tables dès l'import
    from fastapi.testclient import TestClient
    c = TestClient(index.app)  # sans « with » : pas d'événement de démarrage, comme sur Vercel
    r = c.post("/api/auth/register", json={"nom": "V", "email": "v@t.ci", "mot_de_passe": "motdepasse1"})
    assert r.status_code == 200
    assert "Finances Perso" in c.get("/").text
