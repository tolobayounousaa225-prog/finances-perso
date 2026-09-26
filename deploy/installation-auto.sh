#!/usr/bin/env bash
# =============================================================================
# Installation COMPLÈTE de Finances Perso sur le VPS, en une seule commande.
# À lancer en root sur le VPS :
#   curl -fsSL https://raw.githubusercontent.com/tolobayounousaa225-prog/finances-perso/main/deploy/installation-auto.sh | bash
#
# Ce que fait le script (on peut le relancer sans risque, il ne refait que ce qui manque) :
#   1. installe Docker et git s'ils manquent ;
#   2. télécharge le code dans /opt/finances-perso ;
#   3. crée le fichier .env (avec une clé secrète aléatoire) ;
#   4. ajoute l'API dans le Caddy existant (installé sur le système OU en conteneur),
#      ou utilise le Caddy de l'application si les ports 80/443 sont libres ;
#   5. lance l'application, puis programme :
#        - la MISE À JOUR AUTOMATIQUE toutes les 5 minutes (le serveur récupère lui-même
#          les nouveautés de GitHub : aucune clé SSH ni secret à configurer) ;
#        - une SAUVEGARDE de la base chaque nuit à 3 h.
# Il ne touche pas aux autres sites déjà présents sur le serveur.
# =============================================================================
set -euo pipefail

DEPOT="https://github.com/tolobayounousaa225-prog/finances-perso.git"
DOSSIER="/opt/finances-perso"
PAGES="https://tolobayounousaa225-prog.github.io"
PORT_API=8000

etape() { echo; echo "==== $* ===="; }
[ "$(id -u)" -eq 0 ] || { echo "Lance ce script en root (connecte-toi avec ssh root@...)"; exit 1; }

IP=$(curl -fsS -4 https://ifconfig.me || hostname -I | awk '{print $1}')
DOMAINE="api.${IP//./-}.sslip.io"

# -----------------------------------------------------------------------------
etape "1/5 Docker et git"
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
echo "OK : $(docker --version)"

# -----------------------------------------------------------------------------
etape "2/5 Code de l'application"
mkdir -p "$DOSSIER"
cd "$DOSSIER"
if [ ! -d .git ]; then
  git init -q
  git remote add origin "$DEPOT"
fi
git fetch -q origin main
git reset -q --hard origin/main   # garde le fichier .env (il n'est pas dans git)
echo "OK : version $(git log -1 --format='%h - %s')"

# -----------------------------------------------------------------------------
etape "3/5 Réglages (.env)"
if [ -f .env ]; then
  echo "OK : .env existe déjà, je le garde"
  PORT_API=$(grep -oP '^PORT_API=\K[0-9]+' .env || echo 8000)
else
  # Choisit un port local libre (8000 s'il n'est pas déjà utilisé par une autre application)
  while ss -tln 2>/dev/null | grep -q ":$PORT_API "; do PORT_API=$((PORT_API + 1)); done
  cat > .env <<EOF
SECRET_KEY=$(openssl rand -hex 32)
FRONTEND_ORIGINS=$PAGES
DOMAINE=$DOMAINE
COMPOSE_PROFILES=
PORT_API=$PORT_API
APP_URL=$PAGES/finances-perso/
EOF
  chmod 600 .env
  echo "OK : .env créé"
fi

# -----------------------------------------------------------------------------
etape "4/5 HTTPS avec Caddy"
BLOC_SYSTEME=$(printf '\n%s {\n    reverse_proxy 127.0.0.1:%s\n}\n' "$DOMAINE" "$PORT_API")
BLOC_DOCKER=$(printf '\n%s {\n    reverse_proxy finances-api:8000\n}\n' "$DOMAINE")
CADDY_CONTENEUR=$(docker ps --format '{{.Names}} {{.Image}}' | awk 'tolower($2) ~ /caddy/ {print $1; exit}')
MODE_CADDY=""

if systemctl is-active --quiet caddy 2>/dev/null; then
  MODE_CADDY="systeme"
  if ! grep -q "$DOMAINE" /etc/caddy/Caddyfile; then
    cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.avant-finances"
    echo "$BLOC_SYSTEME" >> /etc/caddy/Caddyfile
  fi
  caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
    || { cp /etc/caddy/Caddyfile.avant-finances /etc/caddy/Caddyfile; echo "ERREUR : config Caddy invalide, j'ai remis l'ancienne. Envoie ce message à Claude."; exit 1; }
  systemctl reload caddy
  echo "OK : site ajouté au Caddy du système"
elif [ -n "$CADDY_CONTENEUR" ]; then
  MODE_CADDY="conteneur"
  echo "Caddy trouvé dans le conteneur « $CADDY_CONTENEUR » (configuré après le lancement)"
elif ! ss -tln 2>/dev/null | grep -qE ':(80|443) '; then
  MODE_CADDY="inclus"
  sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=caddy/' .env
  echo "OK : ports 80/443 libres, j'utilise le Caddy de l'application"
else
  echo "ATTENTION : les ports 80/443 sont occupés par autre chose que Caddy :"
  ss -tlnp | grep -E ':(80|443) ' || true
  echo "Envoie ce message à Claude. Je lance quand même l'application."
fi

# -----------------------------------------------------------------------------
etape "5/5 Lancement et automatisations"
docker compose up -d --build

if [ "$MODE_CADDY" = "conteneur" ]; then
  docker network connect finances-perso "$CADDY_CONTENEUR" 2>/dev/null || true
  FICHIER=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$CADDY_CONTENEUR")
  [ -z "$FICHIER" ] && FICHIER=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy"}}{{.Source}}/Caddyfile{{end}}{{end}}' "$CADDY_CONTENEUR")
  if [ -n "$FICHIER" ] && [ -f "$FICHIER" ]; then
    grep -q "$DOMAINE" "$FICHIER" || { cp "$FICHIER" "$FICHIER.avant-finances"; echo "$BLOC_DOCKER" >> "$FICHIER"; }
    docker exec "$CADDY_CONTENEUR" caddy reload --config /etc/caddy/Caddyfile \
      && echo "OK : site ajouté au Caddy du conteneur ($FICHIER)" \
      || echo "ERREUR : Caddy n'a pas accepté la config. Envoie ce message à Claude."
  else
    echo "ATTENTION : je n'ai pas trouvé le Caddyfile du conteneur $CADDY_CONTENEUR. Envoie ce message à Claude."
  fi
fi

# Mise à jour automatique : toutes les 5 minutes, si GitHub a une nouvelle version, on la déploie.
cat > /usr/local/bin/finances-maj <<EOF
#!/usr/bin/env bash
set -e
cd $DOSSIER
git fetch -q origin main
if [ "\$(git rev-parse HEAD)" != "\$(git rev-parse origin/main)" ]; then
  echo "\$(date '+%F %T') nouvelle version : \$(git log -1 --format=%s origin/main)"
  git reset -q --hard origin/main
  docker compose up -d --build
  docker image prune -f >/dev/null
fi
EOF
chmod +x /usr/local/bin/finances-maj
cat > /etc/cron.d/finances-perso <<EOF
*/5 * * * * root /usr/local/bin/finances-maj >> /var/log/finances-maj.log 2>&1
0 3 * * * root $DOSSIER/deploy/sauvegarde.sh >> /var/log/finances-sauvegarde.log 2>&1
EOF
chmod +x "$DOSSIER/deploy/"*.sh
echo "OK : mise à jour automatique (toutes les 5 min) et sauvegarde (chaque nuit à 3 h) programmées"

# Vérification
echo "Attente du démarrage de l'application..."
for _ in $(seq 30); do
  curl -fsS "http://127.0.0.1:$PORT_API/api/health" >/dev/null 2>&1 && break
  sleep 2
done

echo
echo "================================================================"
if curl -fsS "http://127.0.0.1:$PORT_API/api/health" >/dev/null 2>&1; then
  echo "✅ L'application tourne sur le serveur."
else
  echo "❌ L'application ne répond pas. Envoie à Claude le résultat de :"
  echo "   docker compose -f $DOSSIER/docker-compose.yml logs --tail 50"
fi
echo
echo "Adresse de l'API : https://$DOMAINE"
echo
echo "Dernière étape, sur GitHub (Settings > Secrets and variables > Actions > Variables) :"
echo "   créer la variable  API_URL  =  https://$DOMAINE"
echo "================================================================"
