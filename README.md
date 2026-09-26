# Finances Perso

Application web de gestion financière personnelle (montants en FCFA, plusieurs comptes).

- **Revenus et dépenses** : saisie rapide, avec la catégorie détectée en direct pendant la frappe.
- **Catégorisation automatique** par mots-clés (Orange, CIE, Yango, marché, loyer…). Si une dépense est mal classée, on la corrige dans la liste : l'app **apprend** le mot-clé pour les prochaines dépenses.
- **Recommandations** basées sur des règles : 50/30/20, budget dépassé ou presque atteint, hausse inhabituelle d'une catégorie, fonds d'urgence, dépenses supérieures aux revenus.
- **Budgets mensuels** par catégorie.
- **Bilan PDF** du mois (chiffres clés, catégories et budgets, objectifs, recommandations, mouvements) : bouton dans *Mouvements* et *Paramètres*.
- **Conseils rédigés par l'IA** (Claude, facultatif) : sur le tableau de bord, bouton « Demander des conseils à l'IA ». Voir la section « Conseils IA » plus bas.
- **Mouvements récurrents** : salaire, loyer, abonnements… saisis une fois, puis créés **automatiquement** chaque mois le jour choisi (le dernier jour du mois si ce jour n'existe pas). La création a lieu à l'ouverture de l'app et chaque jour avec la tâche planifiée. Un mois manqué est rattrapé, et rien n'est jamais créé deux fois.
- **Objectifs d'épargne** (ex. « 500 000 FCFA pour un ordinateur d'ici juin ») : barre de progression, montant à mettre de côté chaque mois et conseil sur le tableau de bord. Un mouvement récurrent lié à l'objectif fait les versements automatiquement.
- **Traçabilité** : rien n'est supprimé (on archive), et chaque création, modification ou archivage est inscrit dans un journal (valeur avant / après). Export CSV compatible Excel.
- **Emails automatiques** :
  - **bilan mensuel** le 1er du mois à 8 h (revenus, dépenses, principales catégories, recommandations) ;
  - **alerte de budget** dès qu'une catégorie atteint 80 % puis 100 % de son plafond.

  On peut les activer ou les désactiver dans l'onglet Paramètres, avec un aperçu du bilan et un bouton « M'envoyer ce bilan maintenant ».
- **Installable sur téléphone** : logo, icônes et manifeste web. Dans le navigateur du téléphone, utilise « Ajouter à l'écran d'accueil ».
- Chaque compte ne voit que ses propres données.
- **Super admin** : le premier compte créé (ou celui de `SUPERADMIN_EMAIL`) a un onglet *Administration*. Il y voit la liste de tous les comptes et le détail de chacun (tableau de bord, mouvements, journal), **en lecture seule**. Chaque consultation est inscrite dans son journal.

## Structure

```
backend/
  main.py              API FastAPI (routes)
  models.py            tables de la base de données
  database.py          connexion (SQLite par défaut, PostgreSQL via DATABASE_URL)
  auth.py              mots de passe + jetons JWT
  categorisation.py    mots-clés et catégorisation automatique  <- à enrichir !
  recommandations.py   règles de bonne gestion                   <- à enrichir !
  recurrents.py        création automatique des mouvements récurrents
  objectifs.py         progression des objectifs d'épargne
  tracabilite.py       journal de traçabilité
  temps.py             date du jour (fuseau Africa/Abidjan par défaut)
  bilan_pdf.py         bilan mensuel en PDF (fpdf2)
  conseils_ia.py       conseils rédigés par l'IA (Claude, facultatif)
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

Sans configuration, les emails sont **simulés** : ils s'affichent dans les logs du serveur, ce qui est pratique pour développer. Pour un envoi réel, ajoute ces variables dans `.env`, ou sur Vercel dans *Settings → Environment Variables* puis *Redeploy* :

**Avec Gmail** : active la validation en deux étapes sur ton compte Google, puis crée un [mot de passe d'application](https://myaccount.google.com/apppasswords). C'est un code de 16 lettres, à mettre dans `SMTP_PASSWORD` (le mot de passe habituel du compte ne fonctionne pas).

Emails envoyés : **bienvenue** à l'inscription, **bilan mensuel** le 1er du mois, **alertes de budget** à 80 % et 100 %. Sur Vercel, le lien dans les emails pointe automatiquement vers l'adresse de production (`APP_URL` permet de le changer).


| Variable | Exemple | Rôle |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | serveur SMTP |
| `SMTP_PORT` | `587` | 587 (STARTTLS) ou 465 (SSL) |
| `SMTP_USER` | `moi@gmail.com` | identifiant |
| `SMTP_PASSWORD` | `abcd efgh ijkl mnop` | mot de passe (avec Gmail : [mot de passe d'application](https://myaccount.google.com/apppasswords)) |
| `SMTP_FROM` | `Finances Perso <moi@gmail.com>` | expéditeur affiché (facultatif) |
| `APP_URL` | `https://finances-perso-vert.vercel.app` | lien « Ouvrir Finances Perso » (facultatif sur Vercel) |
| `HEURE_BILAN` | `8` | heure d'envoi du bilan (défaut : 8) |
| `FUSEAU_HORAIRE` | `Africa/Abidjan` | fuseau horaire des tâches planifiées |

**Comment ça marche.** Une tâche tourne chaque jour à 8 h. Elle envoie le bilan du mois écoulé à tous ceux qui ne l'ont pas encore reçu, jusqu'au 7 du mois. Si le serveur était arrêté le 1er, le bilan part donc quand même au redémarrage. Chaque envoi est mémorisé en base : un email ne part jamais deux fois. Les utilisateurs sans aucun mouvement le mois précédent ne reçoivent pas de bilan vide.

## Conseils IA (facultatif)

Sans configuration, la carte « Conseils de l'IA » indique simplement que la fonction n'est pas activée : tout le reste marche normalement.

Pour l'activer, il faut une clé de l'API Anthropic. **Attention : ce service est payant** (crédit prépayé, pas d'abonnement). Chaque demande coûte quelques centimes (environ 2 à 5 centimes de dollar avec le modèle par défaut).

1. Crée un compte sur https://console.anthropic.com, ajoute un peu de crédit (*Billing*), puis crée une clé (*API Keys*).
2. Sur Vercel : *Settings → Environment Variables*, ajoute `ANTHROPIC_API_KEY` (la clé), coche *Production*, puis fais un *Redeploy*.

| Variable | Défaut | Rôle |
|---|---|---|
| `ANTHROPIC_API_KEY` | (vide = désactivé) | clé de l'API Anthropic |
| `ANTHROPIC_MODEL` | `claude-opus-5` | modèle utilisé (ex. `claude-haiku-4-5`, bien moins cher) |
| `IA_LIMITE_JOUR` | `5` | demandes maximum par utilisateur et par jour (pour maîtriser le coût) |

Seuls des chiffres agrégés sont envoyés : totaux par catégorie, budgets, noms et montants des objectifs, jamais le nom, l'email ni le détail des mouvements. Les conseils sont gardés en base pour le mois (relire la page ne coûte rien) et figurent dans le bilan PDF.

## Mise en ligne gratuite : Vercel + base Neon (sans carte bancaire)

Vercel (offre *Hobby*, gratuite) fait tourner l'application : l'API et l'interface sont servies à la même adresse. La base de données PostgreSQL gratuite est fournie par Neon, et elle s'ajoute depuis Vercel.

1. Va sur [vercel.com](https://vercel.com) et inscris-toi avec **GitHub** (offre *Hobby*).
2. Clique sur *Add New* → *Project*, **importe** `finances-perso`, puis *Deploy*. Aucun réglage n'est nécessaire : Vercel reconnaît FastAPI et utilise `api/index.py` comme point d'entrée (`vercel.json` déclare seulement la tâche quotidienne et la région).
3. Dans le projet, ouvre l'onglet *Storage* → *Create Database* → **Neon**, puis *Create* et connecte la base au projet. Vercel ajoute tout seul la variable `DATABASE_URL`.
4. Dans l'onglet *Deployments*, lance **Redeploy** sur le dernier déploiement. L'app est en ligne sur `https://finances-perso-xxxx.vercel.app`.

À savoir :
- **Clé secrète** : elle est générée au premier démarrage et gardée en base (on peut aussi fournir `SECRET_KEY`).
- **Région** : `vercel.json` place le serveur à Francfort (`fra1`), à côté de la base Neon (`eu-central-1`). Chaque requête vers la base prend ainsi quelques millisecondes au lieu d'environ 90 ms depuis les États-Unis. Si ta base est ailleurs, `/api/diagnostic` affiche `region_base` et `region_serveur` pour vérifier.
- **Tâche quotidienne** : Vercel Cron appelle `/api/taches/bilans` chaque jour à 8 h UTC pour créer les mouvements récurrents dus et envoyer les bilans mensuels (voir `vercel.json`). Définir `CRON_SECRET` protège cet appel.
- **Mises à jour** : chaque push sur `main` redéploie automatiquement.
- **Passage au VPS plus tard** : il suffit de mettre la même `DATABASE_URL` dans le `.env` du VPS. Les données restent dans Neon, il n'y a rien à transférer.

Autres hébergeurs possibles : `render.yaml` a été retiré, car Render demande une carte bancaire. La variable `DB_SCHEMA` permet toujours de partager une base Supabase existante.

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
- [x] Export PDF du bilan (Excel : export CSV)
- [x] Revenus et dépenses récurrents (salaire, loyer) saisis automatiquement chaque mois
- [x] Objectifs d'épargne (ex. « 500 000 FCFA pour un ordinateur d'ici juin »)
- [x] Conseils personnalisés rédigés par une IA (Claude), en plus des règles
- [ ] Import de relevés (Wave, Orange Money, banque) en CSV
