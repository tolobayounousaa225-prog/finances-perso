# Finances Perso

Application web de gestion financière personnelle (montants en FCFA, plusieurs comptes).

- **Revenus et dépenses** : saisie rapide, avec la catégorie détectée en direct pendant la frappe.
- **Catégorisation automatique** par mots-clés (Orange, CIE, Yango, marché, loyer…). Si une dépense est mal classée, on la corrige dans la liste : l'app **apprend** le mot-clé pour les prochaines dépenses.
- **Recommandations** basées sur des règles : 50/30/20, budget dépassé ou presque atteint, hausse inhabituelle d'une catégorie, fonds d'urgence, dépenses supérieures aux revenus.
- **Budgets mensuels** par catégorie.
- **Traçabilité** : rien n'est supprimé (on archive), et chaque création, modification ou archivage est inscrit dans un journal (valeur avant / après). Export CSV compatible Excel.
- Chaque compte ne voit que ses propres données.

## Structure

```
backend/
  main.py              API FastAPI (routes)
  models.py            tables de la base de données
  database.py          connexion (SQLite par défaut, PostgreSQL via DATABASE_URL)
  auth.py              mots de passe + jetons JWT
  categorisation.py    mots-clés et catégorisation automatique  <- à enrichir !
  recommandations.py   règles de bonne gestion                   <- à enrichir !
frontend/              interface (HTML/CSS/JS sans framework, Chart.js local)
  config.js            adresse de l'API (générée au déploiement sur GitHub Pages)
tests/                 tests automatiques (pytest)
deploy/                scripts pour le VPS Contabo
Dockerfile, docker-compose.yml, Caddyfile   déploiement du backend (HTTPS automatique)
.github/workflows/     tests + déploiement automatiques
```

## Lancer en local

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn main:app --reload --app-dir backend
```

Ouvrir http://localhost:8000 puis cliquer sur « Créer un compte ».

Lancer les tests : `pytest`

## Mise en ligne : frontend sur GitHub Pages, backend sur le VPS Contabo

```
Navigateur ──► https://tolobayounousaa225-prog.github.io/finances-perso/   (GitHub Pages : HTML/JS)
     │
     └── appels /api ──► https://api.<IP>.sslip.io   (VPS : Caddy ou Nginx ──► conteneur FastAPI + SQLite)
```

Une fois configuré, **chaque push sur `main` lance les tests puis met à jour le frontend ET le backend tout seuls**.

Pas besoin de nom de domaine : [sslip.io](https://sslip.io) fournit gratuitement une adresse qui pointe vers l'IP du VPS. Par exemple, `api.12-34-56-78.sslip.io` pointe vers `12.34.56.78`. Caddy ou Certbot obtiennent un vrai certificat HTTPS pour cette adresse. Le HTTPS est obligatoire : GitHub Pages est en HTTPS, et le navigateur bloquerait une API en HTTP.

### 1. Diagnostic du VPS (ne modifie rien)
```bash
ssh root@IP_DU_VPS 'bash -s' < deploy/diagnostic-vps.sh
```
Le résultat indique si Docker est installé, ce qui occupe déjà les ports 80/443, et l'adresse `DOMAINE` à utiliser.

### 2. Préparer le VPS (une seule fois)
Sur ton ordinateur, crée une clé SSH réservée au déploiement :
```bash
ssh-keygen -t ed25519 -f ~/.ssh/finances_deploy -N ""
scp deploy/installer-vps.sh root@IP_DU_VPS:
ssh root@IP_DU_VPS "bash installer-vps.sh '$(cat ~/.ssh/finances_deploy.pub)'"
```
Le script installe Docker s'il manque et crée l'utilisateur `deploy`. Il ne touche pas aux sites déjà présents.

### 3. Créer `/opt/finances-perso/.env` sur le VPS
Pars du modèle `.env.example` et remplis :
- `SECRET_KEY` ;
- `DOMAINE` : l'adresse donnée par le diagnostic ;
- `FRONTEND_ORIGINS` et `APP_URL` : `https://tolobayounousaa225-prog.github.io` et `https://tolobayounousaa225-prog.github.io/finances-perso/` ;
- le mode HTTPS :
  - **ports 80/443 libres** : `COMPOSE_PROFILES=caddy`, et c'est tout ;
  - **Nginx déjà installé** : `COMPOSE_PROFILES=` (vide), puis suis les instructions en tête de `deploy/nginx-finances.conf`.

### 4. Configurer GitHub
- *Settings → General → Danger zone* : **Change visibility → Public**. GitHub Pages gratuit exige un dépôt public. Le code devient visible, mais aucune donnée ni aucun secret n'est dans le dépôt : ils restent sur le VPS et dans les secrets GitHub.
- *Settings → Pages → Source* : **GitHub Actions**.
- *Settings → Secrets and variables → Actions* :
  - Secrets : `VPS_HOST` (IP du VPS), `VPS_USER` (`deploy`), `VPS_SSH_KEY` (contenu de `~/.ssh/finances_deploy`).
  - Variables : `API_URL` = `https://<DOMAINE>`, `DEPLOIEMENT_ACTIF` = `true`.

### 5. Déployer
Pousse sur `main`, ou relance le dernier workflow dans l'onglet *Actions*. Vérifie ensuite :
- `https://<DOMAINE>/api/health` doit répondre `{"status":"ok"}` ;
- l'application est sur `https://tolobayounousaa225-prog.github.io/finances-perso/`.

**Sauvegardes** : sur le VPS, lance `crontab -e` et ajoute
`0 3 * * * /opt/finances-perso/deploy/sauvegarde.sh`. La base est alors sauvegardée chaque nuit à 3 h, et les 30 dernières copies sont gardées.

## Feuille de route

- [x] Comptes, revenus/dépenses, catégorisation auto + apprentissage, journal
- [x] Tableau de bord, budgets, recommandations par règles
- [ ] Bilan mensuel envoyé par email le 1er du mois + alerte de budget (APScheduler + SMTP)
- [ ] Export PDF / Excel du bilan
- [ ] Revenus et dépenses récurrents (salaire, loyer) saisis automatiquement chaque mois
- [ ] Objectifs d'épargne (ex. « 500 000 FCFA pour un ordinateur d'ici juin »)
- [ ] Conseils personnalisés rédigés par une IA (Claude), en plus des règles
- [ ] Import de relevés (Wave, Orange Money, banque) en CSV
