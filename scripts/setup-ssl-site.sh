#!/bin/bash
# setup-ssl-site.sh — Obtenir le certificat TLS du site vitrine (sentaa.net + www.sentaa.net).
# À exécuter UNE SEULE FOIS sur le VPS, une fois le site cloné et la gateway déjà démarrée.
#
# Prérequis :
#   - DNS sentaa.net ET www.sentaa.net -> IP du VPS
#   - Repo du site (html/css/js, dépôt séparé du backend) cloné dans /var/www/sentaa.net
#   - La gateway (docker-compose.gateway.yml) déjà UP — c'est elle qui sert le challenge ACME
#     sur le port 80, pas de nginx temporaire ici (contrairement à setup-ssl.sh, exécuté avant
#     que la gateway n'existe).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
GATEWAY_COMPOSE="$REPO_DIR/docker-compose.gateway.yml"
WEBROOT="/var/www/certbot"
EMAIL="${CERTBOT_EMAIL:-contact@sentaa.net}"
SITE_DIR="/var/www/sentaa.net"
CERT_PATH="/etc/letsencrypt/live/sentaa.net/fullchain.pem"

echo "=== Sentaa — SSL pour le site vitrine (sentaa.net) ==="

# ── Le site doit être cloné avant d'émettre le certificat ────────────────────
if [ ! -f "$SITE_DIR/index.html" ]; then
  echo "ERREUR : $SITE_DIR/index.html introuvable."
  echo "Clone d'abord le repo du site :"
  echo "  sudo mkdir -p $SITE_DIR"
  echo "  sudo git clone <url-du-repo-site> $SITE_DIR"
  exit 1
fi

# ── Certificat déjà présent ? ─────────────────────────────────────────────────
if [ -f "$CERT_PATH" ]; then
  echo "Certificat déjà existant — rechargement de la gateway."
  docker compose -f "$GATEWAY_COMPOSE" exec -T nginx nginx -s reload
  echo "Site disponible sur https://sentaa.net"
  exit 0
fi

# ── La gateway doit être UP pour servir le challenge ACME ────────────────────
if ! docker compose -f "$GATEWAY_COMPOSE" ps --status running | grep -q nginx; then
  echo "ERREUR : la gateway nginx n'est pas démarrée."
  echo "Lance d'abord : docker compose -f docker-compose.gateway.yml up -d"
  exit 1
fi

mkdir -p "$WEBROOT"

# ── Certificat (apex + www) ───────────────────────────────────────────────────
echo "[1/2] Demande du certificat Let's Encrypt pour sentaa.net + www.sentaa.net..."
docker run --rm \
  -v /etc/letsencrypt:/etc/letsencrypt \
  -v "$WEBROOT:/var/www/certbot" \
  certbot/certbot certonly \
  --webroot -w /var/www/certbot \
  -d sentaa.net \
  -d www.sentaa.net \
  --email "$EMAIL" \
  --agree-tos --no-eff-email \
  --non-interactive

# ── Recharger la gateway avec les nouveaux blocs serveur ──────────────────────
echo "[2/2] Rechargement de la gateway nginx..."
docker compose -f "$GATEWAY_COMPOSE" up -d --force-recreate

echo ""
echo "=== Terminé ==="
echo "  https://sentaa.net       -> site vitrine"
echo "  https://www.sentaa.net   -> redirige vers sentaa.net"
echo ""
echo "Pour mettre à jour le site ensuite :"
echo "  cd $SITE_DIR && sudo git pull"
echo "  (nginx sert les fichiers directement, pas besoin de redémarrer)"
