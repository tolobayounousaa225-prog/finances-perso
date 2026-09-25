# Utiliser le Caddy déjà installé sur le VPS

Ton Caddy occupe déjà les ports 80/443 : on n'utilise **pas** le Caddy de l'application.
Dans `/opt/finances-perso/.env`, mets donc :

```
COMPOSE_PROFILES=
```

Il suffit ensuite d'ajouter un site dans ton Caddy. Commence par regarder comment il est installé :

```bash
systemctl is-active caddy                 # « active » = cas A (installé sur le système)
docker ps --format '{{.Names}} {{.Image}}' | grep -i caddy   # une ligne = cas B (conteneur)
```

Remplace `api.12-34-56-78.sslip.io` par ta valeur de `DOMAINE`.

## Cas A : Caddy installé sur le système

Ajoute à la fin de `/etc/caddy/Caddyfile` :

```
api.12-34-56-78.sslip.io {
    reverse_proxy 127.0.0.1:8000
}
```

Remplace `8000` par la valeur de `PORT_API` si tu l'as changée. Puis :

```bash
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

## Cas B : Caddy dans un conteneur Docker

L'application crée un réseau Docker nommé `finances-perso`. Ton Caddy doit y être connecté.

1. Après le premier déploiement de l'API, connecte ton conteneur Caddy au réseau
   (remplace `mon-caddy` par le nom affiché par `docker ps`) :
   ```bash
   docker network connect finances-perso mon-caddy
   ```
   Si ton Caddy est lancé avec un `docker-compose.yml`, ajoute plutôt le réseau dans ce fichier,
   pour qu'il soit conservé quand le conteneur est recréé :
   ```yaml
   services:
     caddy:
       networks: [default, finances-perso]
   networks:
     finances-perso:
       external: true
   ```
2. Ajoute dans le Caddyfile de ce conteneur :
   ```
   api.12-34-56-78.sslip.io {
       reverse_proxy finances-api:8000
   }
   ```
3. Recharge la configuration :
   ```bash
   docker exec mon-caddy caddy reload --config /etc/caddy/Caddyfile
   ```

## Vérifier

`https://api.12-34-56-78.sslip.io/api/health` doit répondre `{"status":"ok"}`.
Caddy obtient le certificat HTTPS tout seul à la première visite (quelques secondes).
