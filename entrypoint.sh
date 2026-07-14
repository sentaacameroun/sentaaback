#!/bin/sh
set -e

wait_for_tcp() {
    name="$1"; host="$2"; port="$3"
    echo "Attente de $name ($host:$port)..."
    python - "$host" "$port" <<'PY'
import socket
import sys
import time

host, port = sys.argv[1], int(sys.argv[2])
for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit(f"Timeout en attendant {host}:{port}")
PY
}

[ -n "$POSTGRES_HOST" ] && wait_for_tcp postgres "$POSTGRES_HOST" "${POSTGRES_PORT:-5432}"

if [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "Migrations..."
    python manage.py migrate --noinput

    echo "Collectstatic (peuple le volume partagé avec nginx, vide au premier démarrage)..."
    python manage.py collectstatic --noinput

    if [ -n "$DJANGO_SUPERUSER_PASSWORD" ] && [ -n "$DJANGO_SUPERUSER_PHONE_NUMBER" ]; then
        echo "Création du superuser (idempotent, ignore si déjà existant)..."
        python manage.py createsuperuser --noinput || true
    fi
fi

exec "$@"
