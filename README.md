# Finances Perso

Application web de gestion financière personnelle (montants en FCFA, plusieurs comptes).

- **Revenus et dépenses** : saisie rapide, avec la catégorie détectée en direct pendant la frappe.
- **Catégorisation automatique** par mots-clés (Orange, CIE, Yango, marché, loyer…). Si une dépense est mal classée, on la corrige dans la liste : l'app **apprend** le mot-clé pour les prochaines dépenses.
- **Recommandations** basées sur des règles : 50/30/20, budget dépassé ou presque atteint, hausse inhabituelle d'une catégorie, fonds d'urgence, dépenses supérieures aux revenus.
- **Budgets mensuels** par catégorie.
- **Traçabilité** : rien n'est supprimé (on archive), et chaque création, modification ou archivage est inscrit dans un journal (valeur avant / après). Export CSV compatible Excel.
- **Emails automatiques** :
  - **bilan mensuel** le 1er du mois à 8 h (revenus, dépenses, principales catégories, recommandations) ;
  - **alerte de budget** dès qu'une catégorie atteint 80 % puis 100 % de son plafond.

  On peut les activer ou les désactiver dans l'onglet Paramètres, avec un aperçu du bilan et un bouton « M'envoyer ce bilan maintenant ».
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
  statistiques.py      calculs (totaux du mois, moyennes…)
  notifications.py     contenu du bilan mensuel et des alertes de budget
  emails.py            envoi SMTP (ou simulation en local)
  taches.py            tâches planifiées (APScheduler)
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

## Emails (SMTP)

Sans configuration, les emails sont **simulés** : ils s'affichent dans les logs du serveur, ce qui est pratique pour développer. Pour un envoi réel, ajoute ces variables dans `.env` :

| Variable | Exemple | Rôle |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | serveur SMTP |
| `SMTP_PORT` | `587` | 587 (STARTTLS) ou 465 (SSL) |
| `SMTP_USER` | `moi@gmail.com` | identifiant |
| `SMTP_PASSWORD` | `abcd efgh ijkl mnop` | mot de passe (avec Gmail : [mot de passe d'application](https://myaccount.google.com/apppasswords)) |
| `SMTP_FROM` | `Finances Perso <moi@gmail.com>` | expéditeur affiché (facultatif) |
| `APP_URL` | `https://tolobayounousaa225-prog.github.io/finances-perso/` | lien « Ouvrir Finances Perso » dans les emails |
| `HEURE_BILAN` | `8` | heure d'envoi du bilan (défaut : 8) |
| `FUSEAU_HORAIRE` | `Africa/Abidjan` | fuseau horaire des tâches planifiées |

**Comment ça marche.** Une tâche tourne chaque jour à 8 h. Elle envoie le bilan du mois écoulé à tous ceux qui ne l'ont pas encore reçu, jusqu'au 7 du mois. Si le serveur était arrêté le 1er, le bilan part donc quand même au redémarrage. Chaque envoi est mémorisé en base : un email ne part jamais deux fois. Les utilisateurs sans aucun mouvement le mois précédent ne reçoivent pas de bilan vide.

## Mise en ligne gratuite : Render + Supabase (sans carte bancaire)

Render (offre gratuite) fait tourner l'application : l'API et l'interface sont servies à la même adresse. Supabase (offre gratuite) garde les données dans PostgreSQL.

1. **Supabase** : crée un projet `finances-perso`, **note son mot de passe**, puis clique sur *Connect*. Copie l'adresse **Session pooler**, qui commence par `postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-...pooler.supabase.com:5432/postgres`, et remplace `[YOUR-PASSWORD]` par ton mot de passe.
2. **Render** : *New* → *Blueprint* → choisis ce dépôt. Render lit `render.yaml`, génère `SECRET_KEY` tout seul et demande `DATABASE_URL` : colle l'adresse de l'étape 1.
3. Ouvre l'adresse `https://finances-perso-xxxx.onrender.com` donnée par Render.

Les tables sont rangées dans le schéma `finances` (variable `DB_SCHEMA`), qui peut donc partager une base existante. Chaque push sur `main` redéploie automatiquement.

⚠️ Sur l'offre gratuite, Render met l'application en veille après 15 minutes sans visite : le premier chargement suivant prend environ 1 minute. Les bilans mensuels manqués pendant la veille partent au réveil (rattrapage jusqu'au 7 du mois).

## Mise en ligne : frontend sur GitHub Pages, backend sur le VPS Contabo

```
Navigateur ──► https://tolobayounousaa225-prog.github.io/finances-perso/   (GitHub Pages : HTML/JS)
     │
     └── appels /api ──► https://api.<IP>.sslip.io   (VPS : Caddy ──► conteneur FastAPI + SQLite)
```

Une fois configuré, **chaque push sur `main` lance les tests puis met à jour le frontend ET le backend tout seuls**.

Pas besoin de nom de domaine : [sslip.io](https://sslip.io) fournit gratuitement une adresse qui pointe vers l'IP du VPS. Par exemple, `api.12-34-56-78.sslip.io` pointe vers `12.34.56.78`. Caddy ou Certbot obtiennent un vrai certificat HTTPS pour cette adresse. Le HTTPS est obligatoire : GitHub Pages est en HTTPS, et le navigateur bloquerait une API en HTTP.

### Méthode simple (recommandée) : une seule commande

Sur le VPS, connecté en `root` :
```bash
curl -fsSL https://raw.githubusercontent.com/tolobayounousaa225-prog/finances-perso/main/deploy/installation-auto.sh | bash
```
Le script installe tout, configure Caddy (système ou conteneur), lance l'application et programme :
- la **mise à jour automatique** : toutes les 5 minutes, le VPS récupère lui-même les nouveautés de `main`. Aucune clé SSH ni aucun secret n'est à mettre dans GitHub ;
- la **sauvegarde** de la base, chaque nuit à 3 h.

On peut le relancer sans risque. Il reste ensuite, dans GitHub :
- rendre le dépôt **public** ;
- dans *Pages*, choisir la source **GitHub Actions** ;
- créer la variable `API_URL` (le script affiche sa valeur).

Journal des mises à jour sur le VPS : `tail /var/log/finances-maj.log`.

### Méthode détaillée (manuelle)

#### 1. Diagnostic du VPS (ne modifie rien)
```bash
ssh root@IP_DU_VPS 'bash -s' < deploy/diagnostic-vps.sh
```
Le résultat indique si Docker est installé, ce qui occupe déjà les ports 80/443, et l'adresse `DOMAINE` à utiliser.

#### 2. Préparer le VPS (une seule fois)
Sur ton ordinateur, crée une clé SSH réservée au déploiement :
```bash
ssh-keygen -t ed25519 -f ~/.ssh/finances_deploy -N ""
scp deploy/installer-vps.sh root@IP_DU_VPS:
ssh root@IP_DU_VPS "bash installer-vps.sh '$(cat ~/.ssh/finances_deploy.pub)'"
```
Le script installe Docker s'il manque et crée l'utilisateur `deploy`. Il ne touche pas aux sites déjà présents.

#### 3. Créer `/opt/finances-perso/.env` sur le VPS
Pars du modèle `.env.example` et remplis :
- `SECRET_KEY` ;
- `DOMAINE` : l'adresse donnée par le diagnostic ;
- `FRONTEND_ORIGINS` et `APP_URL` : `https://tolobayounousaa225-prog.github.io` et `https://tolobayounousaa225-prog.github.io/finances-perso/` ;
- le mode HTTPS :
  - **ports 80/443 libres** : `COMPOSE_PROFILES=caddy`, et c'est tout ;
  - **Caddy déjà installé** : `COMPOSE_PROFILES=` (vide), puis suis `deploy/caddy-existant.md` ;
  - **Nginx déjà installé** : `COMPOSE_PROFILES=` (vide), puis suis les instructions en tête de `deploy/nginx-finances.conf`.

#### 4. Configurer GitHub
- *Settings → General → Danger zone* : **Change visibility → Public**. GitHub Pages gratuit exige un dépôt public. Le code devient visible, mais aucune donnée ni aucun secret n'est dans le dépôt : ils restent sur le VPS et dans les secrets GitHub.
- *Settings → Pages → Source* : **GitHub Actions**.
- *Settings → Secrets and variables → Actions* :
  - Secrets : `VPS_HOST` (IP du VPS), `VPS_USER` (`deploy`), `VPS_SSH_KEY` (contenu de `~/.ssh/finances_deploy`).
  - Variables : `API_URL` = `https://<DOMAINE>`, `DEPLOIEMENT_ACTIF` = `true`.

#### 5. Déployer
Pousse sur `main`, ou relance le dernier workflow dans l'onglet *Actions*. Vérifie ensuite :
- `https://<DOMAINE>/api/health` doit répondre `{"status":"ok"}` ;
- l'application est sur `https://tolobayounousaa225-prog.github.io/finances-perso/`.

**Sauvegardes** : sur le VPS, lance `crontab -e` et ajoute
`0 3 * * * /opt/finances-perso/deploy/sauvegarde.sh`. La base est alors sauvegardée chaque nuit à 3 h, et les 30 dernières copies sont gardées.

## Feuille de route

- [x] Comptes, revenus/dépenses, catégorisation auto + apprentissage, journal
- [x] Tableau de bord, budgets, recommandations par règles
- [x] Bilan mensuel envoyé par email le 1er du mois + alertes de budget
- [ ] Export PDF / Excel du bilan
- [ ] Revenus et dépenses récurrents (salaire, loyer) saisis automatiquement chaque mois
- [ ] Objectifs d'épargne (ex. « 500 000 FCFA pour un ordinateur d'ici juin »)
- [ ] Conseils personnalisés rédigés par une IA (Claude), en plus des règles
- [ ] Import de relevés (Wave, Orange Money, banque) en CSV
