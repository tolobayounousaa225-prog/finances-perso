#!/usr/bin/env bash
# Sauvegarde quotidienne de la base SQLite (à planifier avec cron sur le VPS) :
#   crontab -e   puis ajouter :   0 3 * * * /opt/finances-perso/deploy/sauvegarde.sh
# Garde les 30 dernières sauvegardes.
set -euo pipefail
DOSSIER=/opt/sauvegardes/finances-perso
mkdir -p "$DOSSIER"
cd /opt/finances-perso
docker compose exec -T app python -c "
import sqlite3; src = sqlite3.connect('/data/finances.db'); dst = sqlite3.connect('/data/backup.db'); src.backup(dst); dst.close()"
docker compose cp app:/data/backup.db "$DOSSIER/finances-$(date +%F).db"
ls -1t "$DOSSIER"/finances-*.db | tail -n +31 | xargs -r rm --
