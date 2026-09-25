#!/usr/bin/env bash
# Diagnostic du VPS avant installation : ne modifie RIEN, affiche seulement des informations.
#   ssh root@IP_DU_VPS 'bash -s' < deploy/diagnostic-vps.sh
echo "=== Système ==="
. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
echo
echo "=== Docker ==="
if command -v docker >/dev/null; then docker --version; docker compose version 2>/dev/null
  echo "Conteneurs en cours :"; docker ps --format '  {{.Names}}  ({{.Image}})  {{.Ports}}'
else echo "Docker n'est pas installé"; fi
echo
echo "=== Qui écoute sur les ports 80, 443 et 8000 ? ==="
if command -v ss >/dev/null; then
  ss -tlnp | awk 'NR==1 || /:(80|443|8000) /'
elif command -v netstat >/dev/null; then
  netstat -tlnp | awk 'NR<=2 || /:(80|443|8000) /'
else
  echo "Outil ss absent (sudo apt install iproute2)"
fi
echo
echo "=== Nginx ==="
if command -v nginx >/dev/null; then nginx -v 2>&1; ls /etc/nginx/sites-enabled 2>/dev/null
else echo "Nginx n'est pas installé"; fi
echo
echo "=== Pare-feu ==="
command -v ufw >/dev/null && ufw status | head -5 || echo "ufw absent"
echo
IP=$(curl -s -4 https://ifconfig.me || hostname -I | awk '{print $1}')
echo "=== Adresse suggérée pour l'API (sans nom de domaine) ==="
echo "IP publique : $IP"
echo "DOMAINE=api.${IP//./-}.sslip.io"
echo
echo "Copie tout ce résultat et envoie-le à Claude."
