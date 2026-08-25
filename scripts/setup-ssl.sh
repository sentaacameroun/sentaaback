#!/bin/bash
# setup-ssl.sh — À exécuter UNE SEULE FOIS sur le VPS pour initialiser le TLS + la gateway.
# Peut être lancé depuis n'importe quel checkout du repo (staging ou prod).
#
# Prérequis :
#   - Docker installé, port 80 libre au moment du lancement
#   - DNS api.sentaa.net ET dev.sentaa.net -> IP du VPS
#
# Émet un unique certificat SAN couvrant les deux domaines, démarre la gateway, et installe
# le renouvellement automatique (cron -> scripts/renew-ssl.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
GATEWAY_COMPOSE="$REPO_DIR/docker-compose.gateway.yml"
WEBROOT="/var/www/certbot"
EMAIL="${CERTBOT_EMAIL:-contact@sentaa.net}"
PRIMARY_DOMAIN="api.sentaa.net"
RENEW_SCRIPT="$SCRIPT_DIR/renew-ssl.sh"

echo "=== Sentaa — Initialisation SSL & Gateway ==="

# ── Certificat déjà présent ? ─────────────────────────────────────────────────
if [ -f "/etc/letsencrypt/live/$PRIMARY_DOMAIN/fullchain.pem" ]; then
  echo "Certificat déjà existant — démarrage de la gateway."
  docker compose -f "$GATEWAY_COMPOSE" up -d
  echo "=== Gateway démarrée ==="
  echo "  https://api.sentaa.net  (prod)"
  echo "  https://dev.sentaa.net  (staging)"
  exit 0
fi

mkdir -p "$WEBROOT"

# ── 1. Nginx temporaire sur le port 80 pour le challenge ACME ─────────────────
echo "[1/3] Nginx HTTP temporaire (challenge ACME)..."
docker run -d --rm --name ssl-init-nginx \
  -p 80:80 \
  -v "$WEBROOT:/usr/share/nginx/html:ro" \
  nginx:stable-alpine
trap 'docker stop ssl-init-nginx >/dev/null 2>&1 || true' EXIT
sleep 3

# ── 2. Certificat SAN (api + dev) ─────────────────────────────────────────────
echo "[2/3] Demande du certificat Let's Encrypt (api.sentaa.net + dev.sentaa.net)..."
docker run --rm \
  -v /etc/letsencrypt:/etc/letsencrypt \
  -v "$WEBROOT:/var/www/certbot" \
  certbot/certbot certonly \
  --webroot -w /var/www/certbot \
  -d api.sentaa.net \
  -d dev.sentaa.net \
  --email "$EMAIL" \
  --agree-tos --no-eff-email \
  --non-interactive

docker stop ssl-init-nginx >/dev/null 2>&1 || true
trap - EXIT

# ── 3. Démarrage de la gateway ────────────────────────────────────────────────
echo "[3/3] Démarrage de la gateway nginx..."
docker compose -f "$GATEWAY_COMPOSE" up -d

# ── Renouvellement automatique (cron : 1er et 15 de chaque mois à 3h) ─────────
CRON_JOB="0 3 1,15 * * $RENEW_SCRIPT >> /var/log/certbot-renew.log 2>&1"
(crontab -l 2>/dev/null | grep -v "renew-ssl.sh"; echo "$CRON_JOB") | crontab -

echo ""
echo "=== SSL & Gateway configurés ==="
echo "  https://api.sentaa.net  (prod — quand le stack prod est up)"
echo "  https://dev.sentaa.net  (staging — quand le stack staging est up)"
echo "  Renouvellement auto : 1er et 15 de chaque mois à 3h"
