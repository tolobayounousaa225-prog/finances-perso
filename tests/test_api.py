"""
Tests automatiques. Lancer avec :  pytest
Chaque test utilise une base SQLite neuve dans un dossier temporaire.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Modules rechargés à chaque test (ils lisent les variables d'environnement à l'import)
MODULES_APP = ["main", "database", "models", "auth", "statistiques", "notifications", "taches", "emails", "temps",
               "tracabilite", "objectifs", "recurrents", "recommandations", "bilan_pdf", "conseils_ia", "deux_facteurs", "etiquettes", "analyses"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("ACTIVER_TACHES", "false")
    monkeypatch.delenv("SMTP_HOST", raising=False)  # emails simulés
    for mod in MODULES_APP:
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
    """Emails simulés, sans les emails de bienvenue (testés à part)."""
    from emails import BOITE_DEV
    return [m for m in BOITE_DEV if "Bienvenue" not in m["Subject"]]


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
    for mod in MODULES_APP:
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
    assert client.get("/api/taches/bilans").json() == {"mouvements_recurrents": 0, "bilans_envoyes": 0}
    monkeypatch.setenv("CRON_SECRET", "abc")
    assert client.get("/api/taches/bilans").status_code == 401
    r = client.get("/api/taches/bilans", headers={"Authorization": "Bearer abc"})
    assert r.status_code == 200


def test_point_entree_vercel(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/vercel.db")
    monkeypatch.setenv("VERCEL", "1")
    for mod in MODULES_APP + ["index"]:
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


def test_diagnostic(client):
    d = client.get("/api/diagnostic").json()
    assert d["tout_va_bien"] is True, d
    assert "users" in d["etapes"]["base_de_donnees"]["tables"]
    assert client.get("/api/diagnostic").json()["etapes"]["ecriture"]["ok"]  # rollback : relançable


def test_erreur_inattendue_renvoie_son_type(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "hacher", lambda _: (_ for _ in ()).throw(RuntimeError("boum")))
    from fastapi.testclient import TestClient
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.post("/api/auth/register", json={"nom": "A", "email": "x@t.ci", "mot_de_passe": "motdepasse1"})
    assert r.status_code == 500 and r.json()["detail"] == "Erreur interne du serveur (RuntimeError)"


# ---------------------------------------------------------------------------
# Super admin
# ---------------------------------------------------------------------------
def test_premier_compte_super_admin(client):
    h_admin = inscrire(client, "admin@test.ci")
    h_awa = inscrire(client, "awa2@test.ci")
    assert client.get("/api/me", headers=h_admin).json()["role"] == "superadmin"
    assert client.get("/api/me", headers=h_awa).json()["role"] == "utilisateur"
    assert client.get("/api/admin/utilisateurs?annee=2026&mois=9", headers=h_awa).status_code == 403


def test_super_admin_observe_les_comptes(client):
    h_admin = inscrire(client, "admin@test.ci")
    h_awa = inscrire(client, "awa2@test.ci")
    ajouter(client, h_awa, "revenu", 200000, "Salaire")
    ajouter(client, h_awa, "depense", 5000, "Recharge Orange")

    liste = client.get("/api/admin/utilisateurs?annee=2026&mois=9", headers=h_admin).json()
    awa = next(u for u in liste if u["email"] == "awa2@test.ci")
    assert awa["nb_mouvements"] == 2 and awa["revenus_mois"] == 200000 and awa["depenses_mois"] == 5000

    detail = client.get(f"/api/admin/utilisateurs/{awa['id']}?annee=2026&mois=9", headers=h_admin).json()
    assert detail["tableau_de_bord"]["solde"] == 195000
    assert [m["libelle"] for m in detail["mouvements"]] == ["Recharge Orange", "Salaire"]
    assert len(detail["journal"]) == 2
    # la consultation est tracée dans le journal du super admin, pas dans celui d'Awa
    assert client.get("/api/journal", headers=h_admin).json()[0]["action"] == "consultation"
    assert len(client.get("/api/journal", headers=h_awa).json()) == 2
    # lecture seule : le super admin ne peut pas toucher aux mouvements d'Awa
    mid = client.get("/api/mouvements", headers=h_awa).json()[0]["id"]
    assert client.delete(f"/api/mouvements/{mid}", headers=h_admin).status_code == 404


def test_super_admin_par_email(client, monkeypatch):
    inscrire(client, "premier@test.ci")
    monkeypatch.setenv("SUPERADMIN_EMAIL", "Chef@Test.ci")
    h_chef = inscrire(client, "chef@test.ci")
    assert client.get("/api/me", headers=h_chef).json()["role"] == "superadmin"


def test_migration_ajoute_la_colonne_role(tmp_path, monkeypatch):
    import sqlite3
    chemin = tmp_path / "ancienne.db"
    con = sqlite3.connect(chemin)  # base créée par l'ancienne version, sans colonne role
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, nom VARCHAR NOT NULL, email VARCHAR NOT NULL UNIQUE,"
                " mot_de_passe_hash VARCHAR NOT NULL, recevoir_bilan BOOLEAN NOT NULL,"
                " recevoir_alertes BOOLEAN NOT NULL, created_at DATETIME)")
    con.execute("INSERT INTO users VALUES (1, 'Ancien', 'ancien@test.ci', 'x', 1, 1, NULL)")
    con.commit(); con.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{chemin}")
    monkeypatch.setenv("ACTIVER_TACHES", "false")
    for mod in MODULES_APP:
        sys.modules.pop(mod, None)
    import main
    main.initialiser_base()
    con = sqlite3.connect(chemin)
    assert con.execute("SELECT role FROM users WHERE id = 1").fetchone()[0] == "superadmin"


# ---------------------------------------------------------------------------
# Email de bienvenue, logo, adresse de l'app dans les emails
# ---------------------------------------------------------------------------
def test_email_de_bienvenue(client):
    from emails import BOITE_DEV
    inscrire(client, "nouveau@test.ci")
    mail = next(m for m in BOITE_DEV if "Bienvenue" in m["Subject"])
    assert mail["To"] == "nouveau@test.ci"
    html = mail.get_body(("html",)).get_content()
    assert "/logo-192.png" in html and "Bienvenue, Awa" in html


def test_adresse_app_vercel_dans_les_emails(monkeypatch):
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "finances-perso-vert.vercel.app")
    sys.modules.pop("notifications", None)
    import notifications
    assert notifications.APP_URL == "https://finances-perso-vert.vercel.app"
    sys.modules.pop("notifications", None)


def test_super_admin_email_unique(client, monkeypatch):
    h_premier = inscrire(client, "premier@test.ci")
    assert client.get("/api/me", headers=h_premier).json()["role"] == "superadmin"
    monkeypatch.setenv("SUPERADMIN_EMAIL", "chef@test.ci")
    inscrire(client, "chef@test.ci")
    assert client.get("/api/me", headers=h_premier).json()["role"] == "utilisateur"


def test_logo_et_manifeste_servis(client):
    for fichier in ["logo.svg", "logo-192.png", "logo-512.png", "favicon.ico", "apple-touch-icon.png",
                    "manifest.webmanifest"]:
        assert client.get("/" + fichier).status_code == 200, fichier
    assert client.get("/manifest.webmanifest").json()["short_name"] == "Finances"


def test_envoi_smtp_reel(monkeypatch):
    """Envoi réel vers un petit serveur SMTP local (sans simulation)."""
    aiosmtpd = pytest.importorskip("aiosmtpd.controller")
    recus = []

    class Boite:
        async def handle_DATA(self, server, session, envelope):
            recus.append(envelope)
            return "250 OK"

    serveur = aiosmtpd.Controller(Boite(), hostname="127.0.0.1", port=8025)
    serveur.start()
    try:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", "8025")
        monkeypatch.setenv("SMTP_FROM", "Finances Perso <robot@test.ci>")
        sys.modules.pop("emails", None)
        import emails
        assert emails.envoyer_email("awa@test.ci", "Test", "Bonjour", "<p>Bonjour</p>") is True
    finally:
        serveur.stop()
        sys.modules.pop("emails", None)
    assert recus and recus[0].rcpt_tos == ["awa@test.ci"]
    assert b"Subject: Test" in recus[0].content


def test_diagnostic_emails_sans_valeurs(client, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret-a-ne-pas-montrer")
    monkeypatch.setenv("smtp_user ", "faute-de-frappe")
    r = client.get("/api/diagnostic")
    e = r.json()["emails"]
    assert e["envoi_reel"] is True and "SMTP_PASSWORD" in e["variables_presentes"]
    assert "smtp_user " in e["variables_proches"]  # repère les noms mal écrits
    assert "secret-a-ne-pas-montrer" not in r.text


# ---------------------------------------------------------------------------
# Performance : même résultat avec moins de requêtes
# ---------------------------------------------------------------------------
def test_statistiques_sur_plusieurs_mois(client):
    h = inscrire(client)
    for m in (6, 7, 8):
        ajouter(client, h, "revenu", 200000, "Salaire", d=f"2026-{m:02d}-05")
        ajouter(client, h, "depense", 10000, "Yango", d=f"2026-{m:02d}-06")    # Transport (besoin)
        ajouter(client, h, "depense", 30000, "Tontine", d=f"2026-{m:02d}-07")  # Épargne
    ajouter(client, h, "revenu", 200000, "Salaire", d="2026-09-05")
    ajouter(client, h, "depense", 25000, "Yango", d="2026-09-06")
    ajouter(client, h, "depense", 999999, "Yango", d="2026-02-06")  # hors fenêtre : ignoré

    d = client.get("/api/dashboard?annee=2026&mois=9", headers=h).json()
    assert d["revenus"] == 200000 and d["par_categorie"] == {"Transport": 25000}
    assert [e["depenses"] for e in d["evolution"]] == [0, 0, 40000, 40000, 40000, 25000]
    titres = [r["titre"] for r in d["recommandations"]]
    assert "Hausse des dépenses « Transport »" in titres  # 25 000 contre 10 000 en moyenne
    # épargne cumulée 90 000 ≥ 3 mois × 10 000 de dépenses essentielles
    assert "Fonds d'urgence constitué" in titres
    # l'ancienne route donne toujours les mêmes recommandations
    assert client.get("/api/recommandations?annee=2026&mois=9", headers=h).json() == d["recommandations"]


# ---------------------------------------------------------------- Mouvements récurrents
def fixer_date(monkeypatch, jour):
    """Fait croire à l'application que nous sommes le `jour`."""
    import notifications
    import recurrents
    import main
    import statistiques
    for module in (recurrents, main, statistiques, notifications):
        monkeypatch.setattr(module, "aujourd_hui", lambda: jour)


def test_recurrents_crees_automatiquement(client, monkeypatch):
    from datetime import date
    h = inscrire(client)
    fixer_date(monkeypatch, date(2026, 9, 26))
    # Salaire le 25 depuis juillet : juillet, août et septembre sont créés tout de suite
    r = client.post("/api/recurrents", headers=h, json={"type": "revenu", "montant": 400000, "libelle": "Salaire",
                                                        "jour": 25, "debut": "2026-07-01"})
    assert r.status_code == 201, r.text
    salaire = r.json()
    assert salaire["mouvements_crees"] == 3 and salaire["prochaine_date"] == "2026-10-25"
    # Loyer le 31 : catégorisé automatiquement, et créé le 30 en septembre (mois de 30 jours)
    loyer = client.post("/api/recurrents", headers=h, json={"type": "depense", "montant": 100000, "libelle": "Loyer",
                                                           "jour": 31, "debut": "2026-09-01"}).json()
    assert loyer["categorie_nom"] == "Logement" and loyer["mouvements_crees"] == 0
    assert loyer["prochaine_date"] == "2026-09-30"

    # Ouvrir l'app ne recrée rien ; le 30, le loyer apparaît
    client.get("/api/me", headers=h)
    assert len(client.get("/api/mouvements", headers=h).json()) == 3
    fixer_date(monkeypatch, date(2026, 9, 30))
    client.get("/api/me", headers=h)
    sept = client.get("/api/mouvements?annee=2026&mois=9", headers=h).json()
    assert {(m["libelle"], m["date"]) for m in sept} == {("Salaire", "2026-09-25"), ("Loyer", "2026-09-30")}
    assert all(m["recurrent_id"] for m in sept)

    # Un mouvement créé automatiquement puis archivé n'est pas recréé
    loyer_mvt = next(m for m in sept if m["libelle"] == "Loyer")
    client.delete(f"/api/mouvements/{loyer_mvt['id']}", headers=h)
    client.get("/api/me", headers=h)
    assert len(client.get("/api/mouvements?annee=2026&mois=9", headers=h).json()) == 1

    # Arrêt : plus rien en octobre ; la tâche quotidienne crée le reste pour tout le monde
    client.delete(f"/api/recurrents/{loyer['id']}", headers=h)
    fixer_date(monkeypatch, date(2026, 10, 31))
    assert client.get("/api/taches/bilans").json()["mouvements_recurrents"] == 1  # salaire d'octobre
    octobre = client.get("/api/mouvements?annee=2026&mois=10", headers=h).json()
    assert [m["libelle"] for m in octobre] == ["Salaire"]
    actions = {(j["action"], j["entite"]) for j in client.get("/api/journal", headers=h).json()}
    assert {("creation automatique", "mouvement"), ("arret", "recurrent")} <= actions


def test_recurrents_validation_et_isolation(client, monkeypatch):
    from datetime import date
    h = inscrire(client)
    fixer_date(monkeypatch, date(2026, 9, 26))
    base = {"type": "depense", "montant": 5000, "libelle": "Netflix", "jour": 5}
    assert client.post("/api/recurrents", headers=h, json={**base, "jour": 32}).status_code == 422
    assert client.post("/api/recurrents", headers=h, json={**base, "debut": "2025-01-01"}).status_code == 400
    r = client.post("/api/recurrents", headers=h, json=base).json()
    assert r["categorie_nom"] == "Loisirs" and r["mouvements_crees"] == 0  # le 5 est passé : octobre
    autre = inscrire(client, "kofi@test.ci")
    assert client.get("/api/recurrents", headers=autre).json() == []
    assert client.put(f"/api/recurrents/{r['id']}", headers=autre, json=base).status_code == 404
    assert client.delete(f"/api/recurrents/{r['id']}", headers=autre).status_code == 404


# ---------------------------------------------------------------- Objectifs d'épargne
def test_objectifs_epargne(client, monkeypatch):
    from datetime import date
    h = inscrire(client)
    fixer_date(monkeypatch, date(2026, 9, 26))
    o = client.post("/api/objectifs", headers=h, json={"nom": "Ordinateur", "montant_cible": 500000,
                                                       "date_limite": "2027-06-30"}).json()
    assert o["pourcentage"] == 0 and o["mois_restants"] == 10 and o["par_mois_conseille"] == 50000

    o = client.post(f"/api/objectifs/{o['id']}/versements", headers=h, json={"montant": 100000}).json()
    assert (o["epargne"], o["pourcentage"], o["reste"], o["par_mois_conseille"]) == (100000, 20, 400000, 40000)
    versement = client.get("/api/mouvements", headers=h).json()[0]
    assert versement["categorie_nom"] == "Épargne" and versement["objectif_id"] == o["id"]
    assert versement["date"] == "2026-09-26"  # sans date : aujourd'hui
    r = client.post(f"/api/objectifs/{o['id']}/versements", headers=h, json={"montant": 1000, "date": "2026-09-02"})
    assert r.status_code == 201, r.text
    assert client.get("/api/mouvements?annee=2026&mois=9", headers=h).json()[-1]["date"] == "2026-09-02"
    client.delete(f"/api/mouvements/{client.get('/api/mouvements?annee=2026&mois=9', headers=h).json()[-1]['id']}",
                  headers=h)  # archivé : ne compte plus dans l'objectif

    # Versement automatique chaque mois via un récurrent, compté dans l'objectif
    r = client.post("/api/recurrents", headers=h, json={"type": "revenu", "montant": 40000, "libelle": "Ordi",
                                                        "jour": 1, "debut": "2026-09-01", "objectif_id": o["id"]}).json()
    assert r["type"] == "depense" and r["categorie_nom"] == "Épargne" and r["objectif_nom"] == "Ordinateur"
    assert client.get("/api/objectifs", headers=h).json()[0]["epargne"] == 140000

    # Le tableau de bord conseille le montant mensuel ; les versements comptent comme épargne
    client.post("/api/mouvements", headers=h, json={"type": "revenu", "montant": 400000, "libelle": "Salaire",
                                                    "date": "2026-09-01"})
    d = client.get("/api/dashboard?annee=2026&mois=9", headers=h).json()
    assert d["par_groupe"]["epargne"] == 140000
    reco = next(x for x in d["recommandations"] if "Ordinateur" in x["titre"])
    assert reco["niveau"] == "bravo" and "40 000 FCFA par mois suffit" in reco["message"]
    assert "juin 2027" in reco["message"]
    client.put(f"/api/recurrents/{r['id']}", headers=h, json={"type": "depense", "montant": 20000, "libelle": "Ordi",
                                                             "jour": 1, "debut": "2026-09-01", "objectif_id": o["id"]})
    d = client.get("/api/dashboard?annee=2026&mois=9", headers=h).json()
    reco = next(x for x in d["recommandations"] if "Ordinateur" in x["titre"])
    assert reco["niveau"] == "conseil" and "passe-le à 36 000 FCFA" in reco["message"]

    # Objectif atteint -> bravo ; archivage -> les versements récurrents s'arrêtent
    client.post(f"/api/objectifs/{o['id']}/versements", headers=h, json={"montant": 400000})
    assert client.get("/api/objectifs", headers=h).json()[0]["statut"] == "atteint"
    d = client.get("/api/dashboard?annee=2026&mois=9", headers=h).json()
    assert any(x["niveau"] == "bravo" and "Ordinateur" in x["titre"] for x in d["recommandations"])
    client.delete(f"/api/objectifs/{o['id']}", headers=h)
    assert client.get("/api/objectifs", headers=h).json() == []
    assert client.get("/api/recurrents", headers=h).json()[0]["actif"] is False

    autre = inscrire(client, "kofi@test.ci")
    assert client.post(f"/api/objectifs/{o['id']}/versements", headers=autre, json={"montant": 1}).status_code == 404


# ---------------------------------------------------------------- Export PDF
def test_bilan_pdf(client, monkeypatch):
    from datetime import date
    h = inscrire(client)
    fixer_date(monkeypatch, date(2026, 9, 26))
    ajouter(client, h, "revenu", 400000, "Salaire")
    ajouter(client, h, "depense", 120000, "Loyer d’octobre — appart 🏠")  # caractères hors Latin-1
    alim = next(c for c in client.get("/api/categories", headers=h).json() if c["nom"] == "Alimentation")
    client.put("/api/budgets", headers=h, json={"categorie_id": alim["id"], "montant_mensuel": 50000})
    ajouter(client, h, "depense", 60000, "Marché")
    o = client.post("/api/objectifs", headers=h, json={"nom": "Ordinateur", "montant_cible": 500000,
                                                       "date_limite": "2027-06-30"}).json()
    client.post(f"/api/objectifs/{o['id']}/versements", headers=h, json={"montant": 50000})
    for i in range(60):  # plusieurs pages
        ajouter(client, h, "depense", 500, f"Taxi {i}")
    r = client.get("/api/bilan/pdf?annee=2026&mois=9", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "bilan-2026-09.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF") and len(r.content) > 3000
    assert r.content.count(b"/Type /Page\n") >= 2
    vide = client.get("/api/bilan/pdf?annee=2025&mois=1", headers=h)  # mois sans données
    assert vide.status_code == 200 and vide.content.startswith(b"%PDF")
    assert client.get("/api/bilan/pdf?annee=2026&mois=9").status_code == 401


# ---------------------------------------------------------------- Conseils IA
def test_conseils_ia_desactives_sans_cle(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    h = inscrire(client)
    assert client.get("/api/me", headers=h).json()["ia_configuree"] is False
    assert client.get("/api/conseils-ia?annee=2026&mois=9", headers=h).json()["configuree"] is False
    assert client.post("/api/conseils-ia?annee=2026&mois=9", headers=h).status_code == 400


def faux_client_anthropic(requetes, reponse):
    """Vrai SDK Anthropic, mais les requêtes HTTP sont interceptées (aucun appel réseau, rien de payé)."""
    import anthropic
    import httpx2
    import json as _json

    def repondre(requete):
        requetes.append({"url": str(requete.url), "entetes": dict(requete.headers), "corps": _json.loads(requete.content)})
        return httpx2.Response(200, json=reponse)
    return anthropic.Anthropic(api_key="cle-de-test", max_retries=0,
                               http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(repondre)))


REPONSE_IA = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
              "stop_reason": "end_turn", "stop_sequence": None,
              "usage": {"input_tokens": 500, "output_tokens": 200},
              "content": [{"type": "text", "text": "- Réduis les sorties au maquis : 30 000 FCFA ce mois-ci.\n"
                                                   "- Mets 20 000 FCFA dans ta tontine\n  dès le salaire reçu."}]}


def test_conseils_ia_requete_envoyee():
    import conseils_ia
    from recommandations import StatsMois
    s = StatsMois(revenus=400000, depenses=250000, par_groupe={"besoin": 200000, "envie": 50000},
                  par_categorie={"Logement": 150000, "Loisirs": 50000, "Alimentation": 50000}, budgets={"Loisirs": 40000},
                  objectifs=[{"nom": "Ordi", "montant_cible": 500000, "epargne": 100000, "date_limite": "2027-06-30",
                              "par_mois_conseille": 40000, "versement_auto": 0, "statut": "en_cours", "reste": 400000}])
    requetes = []
    import os as _os
    _os.environ["ANTHROPIC_API_KEY"] = "cle-de-test"
    try:
        texte, modele = conseils_ia.generer_conseils(s, [{"titre": "Budget « Loisirs » dépassé"}], "2026-09",
                                                     client=faux_client_anthropic(requetes, REPONSE_IA))
    finally:
        del _os.environ["ANTHROPIC_API_KEY"]
    assert modele == "claude-opus-5"
    assert conseils_ia.resume_texte(texte) == ["Réduis les sorties au maquis : 30 000 FCFA ce mois-ci.",
                                               "Mets 20 000 FCFA dans ta tontine dès le salaire reçu."]
    (req,) = requetes
    assert req["url"].endswith("/v1/messages?beta=true")
    assert "server-side-fallback-2026-07-01" in req["entetes"]["anthropic-beta"]
    corps = req["corps"]
    assert corps["model"] == "claude-opus-5" and corps["fallbacks"] == "default"
    assert corps["output_config"] == {"effort": "low"} and "français" in corps["system"]
    envoye = corps["messages"][0]["content"]
    assert "Logement" in envoye and "Ordi" in envoye and "Budget « Loisirs » dépassé" in envoye

    # Refus du modèle -> message clair
    refus = {**REPONSE_IA, "stop_reason": "refusal", "content": []}
    _os.environ["ANTHROPIC_API_KEY"] = "cle-de-test"
    try:
        with pytest.raises(conseils_ia.ErreurIA):
            conseils_ia.generer_conseils(s, [], "2026-09", client=faux_client_anthropic([], refus))
    finally:
        del _os.environ["ANTHROPIC_API_KEY"]


def test_conseils_ia_api(client, monkeypatch):
    import main
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    monkeypatch.setenv("IA_LIMITE_JOUR", "2")
    appels = []

    def faux_generer(s, alertes, periode):
        appels.append((s.revenus, periode))
        return REPONSE_IA["content"][0]["text"], "claude-opus-5"
    monkeypatch.setattr(main, "generer_conseils", faux_generer)
    h = inscrire(client)
    ajouter(client, h, "revenu", 400000, "Salaire")
    assert client.get("/api/me", headers=h).json()["ia_configuree"] is True
    assert client.get("/api/conseils-ia?annee=2026&mois=9", headers=h).json()["conseils"] == []

    r = client.post("/api/conseils-ia?annee=2026&mois=9", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()["conseils"]) == 2 and appels == [(400000, "2026-09")]
    # Gardés en base : relire ne rappelle pas l'IA ; ils apparaissent aussi dans le PDF
    assert client.get("/api/conseils-ia?annee=2026&mois=9", headers=h).json()["conseils"] == r.json()["conseils"]
    assert client.get("/api/bilan/pdf?annee=2026&mois=9", headers=h).status_code == 200
    assert len(appels) == 1
    # Limite quotidienne (coût maîtrisé), et chaque demande est journalisée
    assert client.post("/api/conseils-ia?annee=2026&mois=9", headers=h).status_code == 200
    assert client.post("/api/conseils-ia?annee=2026&mois=9", headers=h).status_code == 429
    assert sum(j["entite"] == "conseils_ia" for j in client.get("/api/journal", headers=h).json()) == 2
    # Isolation : un autre compte ne voit pas ces conseils
    autre = inscrire(client, "kofi@test.ci")
    assert client.get("/api/conseils-ia?annee=2026&mois=9", headers=autre).json()["conseils"] == []


# ---------------------------------------------------------------- Double authentification
def connecter(client, email="awa@test.ci", mdp="motdepasse1"):
    return client.post("/api/auth/login", data={"username": email, "password": mdp}).json()


def test_double_authentification(client):
    import pyotp
    h = inscrire(client)
    assert client.get("/api/me", headers=h).json()["deux_facteurs"] is False
    prep = client.post("/api/2fa/preparer", headers=h).json()
    assert prep["qr_svg"].startswith("<svg") or "<svg" in prep["qr_svg"][:200]
    assert prep["lien"].startswith("otpauth://totp/Finances%20Perso:awa%40test.ci?secret=")
    totp = pyotp.TOTP(prep["secret"])
    assert client.post("/api/2fa/activer", headers=h, json={"code": "000000"}).status_code == 400
    r = client.post("/api/2fa/activer", headers=h, json={"code": totp.now()})
    assert r.status_code == 200, r.text
    codes = r.json()["codes_secours"]
    assert len(codes) == 8 and len(set(codes)) == 8
    moi = client.get("/api/me", headers=h).json()
    assert moi["deux_facteurs"] is True and moi["codes_secours_restants"] == 8

    # La connexion demande maintenant le code : le mot de passe seul ne donne pas de jeton de connexion
    etape1 = connecter(client)
    assert etape1["deux_facteurs"] is True and "access_token" not in etape1
    jeton = etape1["jeton_2fa"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {jeton}"}).status_code == 401
    assert client.post("/api/auth/2fa", json={"jeton_2fa": jeton, "code": "123456"}).status_code == 401
    r = client.post("/api/auth/2fa", json={"jeton_2fa": jeton, "code": totp.now()})
    assert r.status_code == 200 and client.get("/api/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    # Un jeton de connexion ne remplace pas le jeton temporaire
    assert client.post("/api/auth/2fa", json={"jeton_2fa": h["Authorization"][7:], "code": totp.now()}).status_code == 401

    # Code de secours : fonctionne une seule fois (en minuscules et sans tiret aussi)
    secours = codes[0].lower().replace("-", "")
    assert client.post("/api/auth/2fa", json={"jeton_2fa": connecter(client)["jeton_2fa"], "code": secours}).status_code == 200
    assert client.post("/api/auth/2fa", json={"jeton_2fa": connecter(client)["jeton_2fa"], "code": secours}).status_code == 401
    assert client.get("/api/me", headers=h).json()["codes_secours_restants"] == 7

    # Désactivation : mot de passe + code exigés
    assert client.post("/api/2fa/desactiver", headers=h, json={"mot_de_passe": "faux", "code": totp.now()}).status_code == 400
    assert client.post("/api/2fa/desactiver", headers=h, json={"mot_de_passe": "motdepasse1", "code": totp.now()}).status_code == 200
    assert "access_token" in connecter(client)
    actions = [j["action"] for j in client.get("/api/journal", headers=h).json() if j["entite"] == "securite"]
    assert {"activation 2fa", "desactivation 2fa", "echec 2fa", "code de secours utilise"} <= set(actions)


def test_2fa_limite_les_essais(client):
    import pyotp
    h = inscrire(client)
    secret = client.post("/api/2fa/preparer", headers=h).json()["secret"]
    client.post("/api/2fa/activer", headers=h, json={"code": pyotp.TOTP(secret).now()})
    jeton = connecter(client)["jeton_2fa"]
    for _ in range(5):
        assert client.post("/api/auth/2fa", json={"jeton_2fa": jeton, "code": "000000"}).status_code == 401
    # Après 5 échecs, même le bon code est refusé pendant 10 minutes
    assert client.post("/api/auth/2fa", json={"jeton_2fa": jeton, "code": pyotp.TOTP(secret).now()}).status_code == 429


# ---------------------------------------------------------------- Étiquettes et recherche
def test_etiquettes(client):
    from etiquettes import hashtags, normaliser_etiquette
    assert normaliser_etiquette("#Voyage Assinie") == "voyage-assinie"
    assert hashtags("Pagne #Mariage et #rentrée, #mariage") == ["mariage", "rentree", "mariage"]
    h = inscrire(client)
    m = ajouter(client, h, "depense", 25000, "Pagne pour la cérémonie #mariage", etiquettes=["Famille Koné"])
    assert m["etiquettes"] == ["famille-kone", "mariage"]
    ajouter(client, h, "depense", 80000, "Traiteur #mariage", d="2026-10-02")
    ajouter(client, h, "depense", 3000, "Taxi", etiquettes=["mariage"])
    ajouter(client, h, "depense", 5000, "Yango #rentree")

    # Modifier la catégorie sans renvoyer les étiquettes : elles sont conservées
    loisirs = next(c for c in client.get("/api/categories", headers=h).json() if c["nom"] == "Loisirs")
    r = client.put(f"/api/mouvements/{m['id']}", headers=h,
                   json={**{k: m[k] for k in ("type", "montant", "libelle", "date")}, "categorie_id": loisirs["id"]})
    assert r.json()["etiquettes"] == ["famille-kone", "mariage"]
    # Liste vide explicite : on retire les étiquettes du champ (le #mariage du libellé reste)
    r = client.put(f"/api/mouvements/{m['id']}", headers=h,
                   json={**{k: m[k] for k in ("type", "montant", "libelle", "date")}, "etiquettes": []})
    assert r.json()["etiquettes"] == ["mariage"]

    liste = {e["nom"]: e for e in client.get("/api/etiquettes", headers=h).json()}
    assert liste["mariage"]["nb"] == 3 and liste["mariage"]["depenses"] == 108000
    assert (liste["mariage"]["du"], liste["mariage"]["au"]) == ("2026-09-10", "2026-10-02")
    assert "rentree" in liste and "famille-kone" not in liste

    # Suivi d'un événement : total sur plusieurs catégories et plusieurs mois
    res = client.get("/api/recherche?etiquette=%23Mariage", headers=h).json()
    assert res["nb"] == 3 and res["total_depenses"] == 108000
    assert [l["mois"] for l in res["par_mois"]] == ["2026-09", "2026-10"]
    assert set(res["par_categorie"]) == {"Loisirs", "Autres", "Transport"}
    assert "etiquettes" in client.get("/api/export/csv", headers=h).text.splitlines()[0]
    # Isolation
    autre = inscrire(client, "kofi@test.ci")
    assert client.get("/api/etiquettes", headers=autre).json() == []
    assert client.get("/api/recherche?etiquette=mariage", headers=autre).json()["nb"] == 0


def test_recherche_filtres(client):
    h = inscrire(client)
    ajouter(client, h, "revenu", 400000, "Salaire septembre", d="2026-09-01")
    ajouter(client, h, "depense", 3000, "Yango Plateau", d="2026-09-05")
    ajouter(client, h, "depense", 4500, "YANGO Cocody", d="2026-10-05")
    ajouter(client, h, "depense", 150000, "Loyer", d="2026-10-01")
    archive = ajouter(client, h, "depense", 2000, "Yango annulé", d="2026-10-06")
    client.delete(f"/api/mouvements/{archive['id']}", headers=h)

    def chercher(**params):
        return client.get("/api/recherche", headers=h, params=params).json()
    assert chercher(q="yango")["nb"] == 2  # insensible à la casse, archives exclues
    assert chercher(q="yango", archives=True)["nb"] == 3
    assert chercher(q="yango", archives=True)["total_depenses"] == 7500  # l'archivé ne compte pas
    assert chercher(q="yango", du="2026-10-01")["total_depenses"] == 4500
    assert chercher(type="revenu")["total_revenus"] == 400000
    assert chercher(montant_min=4000, montant_max=200000, type="depense")["nb"] == 2
    transport = next(c for c in client.get("/api/categories", headers=h).json() if c["nom"] == "Transport")
    assert chercher(categorie_id=transport["id"])["par_categorie"] == {"Transport": 7500}
    assert chercher(au="2026-09-30")["nb"] == 2


# ---------------------------------------------------------------- Analyses
def test_score_sante():
    from analyses import score_sante
    from recommandations import StatsMois
    assert score_sante(StatsMois(0, 0, {}, {}, {}))["score"] is None
    parfait = StatsMois(revenus=500000, depenses=400000,
                        par_groupe={"besoin": 200000, "envie": 100000, "epargne": 100000},
                        par_categorie={"Logement": 150000, "Loisirs": 100000}, budgets={"Loisirs": 120000},
                        epargne_totale=1000000, besoins_moyens=200000)
    r = score_sante(parfait)
    assert r["score"] == 100 and r["niveau"] == "Excellente"
    assert all(c["conseil"] is None for c in r["criteres"])
    difficile = StatsMois(revenus=300000, depenses=390000,
                          par_groupe={"besoin": 210000, "envie": 180000},
                          par_categorie={"Loisirs": 180000}, budgets={"Loisirs": 50000},
                          epargne_totale=0, besoins_moyens=200000)
    r = score_sante(difficile)
    points = {c["nom"]: c["points"] for c in r["criteres"]}
    # Épargne 0, équilibre 20*(1-2*0,3)=8, budgets 0, fonds d'urgence 0, envies 60 % -> 0
    assert points == {"Épargne": 0, "Équilibre": 8, "Budgets": 0, "Fonds d'urgence": 0, "Envies maîtrisées": 0}
    assert r["score"] == 8 and r["niveau"] == "À surveiller"
    assert all(c["conseil"] for c in r["criteres"] if c["points"] < c["max"])
    # Sans budget ni historique : critères neutres (10/20)
    neutre = score_sante(StatsMois(100000, 50000, {"besoin": 50000}, {"Logement": 50000}, {}))
    assert {c["nom"]: c["points"] for c in neutre["criteres"]}["Budgets"] == 10


def test_analyses_api(client):
    h = inscrire(client)
    ajouter(client, h, "revenu", 400000, "Salaire", d="2026-09-01")
    ajouter(client, h, "depense", 100000, "Loyer", d="2026-09-01")   # mardi
    ajouter(client, h, "depense", 30000, "Maquis", d="2026-09-04")   # vendredi
    ajouter(client, h, "depense", 20000, "Maquis", d="2026-09-11")   # vendredi
    ajouter(client, h, "depense", 60000, "Tontine", d="2026-09-20")
    ajouter(client, h, "revenu", 380000, "Salaire", d="2026-08-01")
    ajouter(client, h, "depense", 90000, "Loyer", d="2026-08-02")
    r = client.get("/api/analyses?annee=2026&mois=9", headers=h).json()
    cal = r["calendrier"]
    assert len(cal["jours"]) == 30 and cal["premier_jour_semaine"] == 1  # 1er septembre 2026 : mardi
    assert cal["jours"][0]["depenses"] == 100000 and cal["jours"][3]["nb"] == 1
    assert cal["total_mois"] == 210000 and cal["moyenne_par_jour"] == 7000 and cal["jours_sans_depense"] == 26
    assert cal["plus_gros_jour"]["date"] == "2026-09-01"
    vendredi = next(j for j in cal["semaine"] if j["jour"] == "vendredi")
    assert vendredi["moyenne"] == round(50000 / 13)  # 13 vendredis du 1er juillet au 30 septembre
    assert len(r["evolution_score"]) == 6 and r["evolution_score"][-1]["mois"] == "2026-09"
    assert r["evolution_score"][0]["score"] is None and r["evolution_score"][-1]["score"] == r["score"]["score"]
    assert r["score"]["score"] > 0 and len(r["score"]["criteres"]) == 5
    assert r["moyennes_categories"]["Logement"] == round(190000 / 3)
    assert r["revenus_moyens"] == round(780000 / 3)
    autre = inscrire(client, "kofi@test.ci")
    assert client.get("/api/analyses?annee=2026&mois=9", headers=autre).json()["calendrier"]["total_mois"] == 0
