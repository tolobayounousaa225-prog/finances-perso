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
frontend/index.html    interface (HTML/CSS/JS sans framework, Chart.js local)
tests/                 tests automatiques (pytest)
deploy/                scripts pour le VPS Contabo
Dockerfile, docker-compose.yml, Caddyfile   déploiement (HTTPS automatique)
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
| `APP_URL` | `https://finances.mondomaine.com` | lien « Ouvrir Finances Perso » dans les emails |
| `HEURE_BILAN` | `8` | heure d'envoi du bilan (défaut : 8) |
| `FUSEAU_HORAIRE` | `Africa/Abidjan` | fuseau horaire des tâches planifiées |

**Comment ça marche.** Une tâche tourne chaque jour à 8 h. Elle envoie le bilan du mois écoulé à tous ceux qui ne l'ont pas encore reçu, jusqu'au 7 du mois. Si le serveur était arrêté le 1er, le bilan part donc quand même au redémarrage. Chaque envoi est mémorisé en base : un email ne part jamais deux fois. Les utilisateurs sans aucun mouvement le mois précédent ne reçoivent pas de bilan vide.

## Déploiement automatique sur un VPS Contabo

Une fois configuré, **chaque push sur `main` lance les tests puis met le site à jour tout seul**.

1. **Créer une clé SSH de déploiement** sur ton ordinateur :
   `ssh-keygen -t ed25519 -f ~/.ssh/finances_deploy -N ""`
2. **Préparer le VPS** (Ubuntu), une seule fois, en root :
   ```bash
   scp deploy/installer-vps.sh root@IP_DU_VPS:
   ssh root@IP_DU_VPS "bash installer-vps.sh '$(cat ~/.ssh/finances_deploy.pub)'"
   ```
   Le script installe Docker, crée l'utilisateur `deploy` et configure le pare-feu.
3. **Créer le fichier `.env`** sur le VPS, dans `/opt/finances-perso/.env` (modèle : `.env.example`) :
   - `SECRET_KEY` : une longue chaîne aléatoire ;
   - `DOMAINE` : ton nom de domaine, qui doit pointer vers l'IP du VPS (HTTPS automatique grâce à Caddy). Sans nom de domaine, mets `DOMAINE=:80`.
4. **Dans GitHub**, aller dans *Settings → Secrets and variables → Actions* :
   - Secrets : `VPS_HOST` (IP du VPS), `VPS_USER` (`deploy`), `VPS_SSH_KEY` (contenu de `~/.ssh/finances_deploy`).
   - Variables : `DEPLOIEMENT_ACTIF` = `true`.
5. Pousser sur `main` : l'onglet *Actions* montre les tests puis le déploiement.

**Sauvegardes** : sur le VPS, `crontab -e` puis ajouter
`0 3 * * * /opt/finances-perso/deploy/sauvegarde.sh` (sauvegarde chaque nuit à 3 h, garde les 30 dernières).

## Feuille de route

- [x] Comptes, revenus/dépenses, catégorisation auto + apprentissage, journal
- [x] Tableau de bord, budgets, recommandations par règles
- [x] Bilan mensuel envoyé par email le 1er du mois + alertes de budget
- [ ] Export PDF / Excel du bilan
- [ ] Revenus et dépenses récurrents (salaire, loyer) saisis automatiquement chaque mois
- [ ] Objectifs d'épargne (ex. « 500 000 FCFA pour un ordinateur d'ici juin »)
- [ ] Conseils personnalisés rédigés par une IA (Claude), en plus des règles
- [ ] Import de relevés (Wave, Orange Money, banque) en CSV
