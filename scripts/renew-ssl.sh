#!/bin/bash
# renew-ssl.sh — Renouvellement des certificats Let's Encrypt + rechargement de la gateway.
# Appelé automatiquement par cron (installé par scripts/setup-ssl.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATEWAY_COMPOSE="$(dirname "$SCRIPT_DIR")/docker-compose.gateway.yml"
WEBROOT="/var/www/certbot"

echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Renouvellement SSL..."

docker run --rm \
  -v /etc/letsencrypt:/etc/letsencrypt \
  -v "$WEBROOT:/var/www/certbot" \
  certbot/certbot renew \
  --webroot -w /var/www/certbot \
  --quiet

# Recharge nginx pour prendre en compte un éventuel nouveau certificat (no-op sinon).
docker compose -f "$GATEWAY_COMPOSE" exec -T nginx nginx -s reload || \
  docker compose -f "$GATEWAY_COMPOSE" up -d

echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] OK"
