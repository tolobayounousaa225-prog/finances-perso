#!/usr/bin/env bash
# Préparation initiale d'un VPS Contabo (Ubuntu) — à lancer UNE fois en root :
#   bash installer-vps.sh "ssh-ed25519 AAAA...cle-publique-de-deploiement"
set -euo pipefail

CLE_PUBLIQUE="${1:?Donne la clé publique SSH de déploiement en argument}"

# 1. Docker
apt-get update
apt-get install -y ca-certificates curl ufw
curl -fsSL https://get.docker.com | sh

# 2. Utilisateur « deploy » qui peut lancer Docker (utilisé par GitHub Actions)
id deploy &>/dev/null || useradd -m -s /bin/bash deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo "$CLE_PUBLIQUE" >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

# 3. Dossier de l'application
install -d -o deploy -g deploy /opt/finances-perso

# 4. Pare-feu : SSH + HTTP + HTTPS uniquement
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw --force enable

echo "VPS prêt. Crée maintenant /opt/finances-perso/.env (voir .env.example)."
