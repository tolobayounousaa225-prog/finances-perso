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
    for mod in ["main", "database", "models", "auth"]:  # recharge avec la nouvelle base
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
