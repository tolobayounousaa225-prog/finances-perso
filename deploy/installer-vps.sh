#!/usr/bin/env bash
# Préparation d'un VPS (Ubuntu/Debian), même s'il héberge déjà d'autres sites.
# À lancer UNE fois en root :
#   bash installer-vps.sh "ssh-ed25519 AAAA...cle-publique-de-deploiement"
# Le script ne touche pas aux sites existants et n'active pas de pare-feu s'il n'y en a pas.
set -euo pipefail

CLE_PUBLIQUE="${1:?Donne la clé publique SSH de déploiement en argument}"

# 1. Docker (seulement s'il manque)
if ! command -v docker >/dev/null; then
  apt-get update && apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com | sh
fi

# 2. Utilisateur « deploy » qui peut lancer Docker (utilisé par GitHub Actions)
id deploy &>/dev/null || useradd -m -s /bin/bash deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
touch /home/deploy/.ssh/authorized_keys
grep -qxF "$CLE_PUBLIQUE" /home/deploy/.ssh/authorized_keys || echo "$CLE_PUBLIQUE" >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

# 3. Dossier de l'application
install -d -o deploy -g deploy /opt/finances-perso

# 4. Pare-feu : si ufw est déjà actif, on ouvre juste HTTP/HTTPS
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp
  ufw allow 443/tcp
fi

echo "VPS prêt. Crée maintenant /opt/finances-perso/.env (voir .env.example)."
