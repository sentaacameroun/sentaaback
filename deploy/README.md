# Docker : développement local vs production (VPS)

Deux fichiers compose totalement séparés, pas de merge d'override entre eux :

- **`docker-compose.yml`** — développement local. Pas de nginx/TLS, code monté en bind (édition
  sans rebuild), `runserver` (HTTP + WebSockets via Channels), ports Postgres/Redis exposés
  directement pour inspection (psql, client GUI).
- **`docker-compose.prod.yml`** — production VPS. `nginx` (reverse-proxy + TLS) devant `web`
  (Django ASGI, gunicorn+uvicorn) + `celery_worker` + `celery_beat` + `flower` (monitoring,
  loopback uniquement) → `db` (Postgres) + `redis` (cache, channel layer Channels, broker Celery).

Les deux partagent le même `Dockerfile`/`entrypoint.sh` : seul le conteneur `web` exécute
`migrate`/`collectstatic`/`createsuperuser` (variable `RUN_MIGRATIONS=true`), les autres attendent
son statut `healthy` avant de démarrer — évite que 4 conteneurs partageant la même image ne se
marchent dessus en essayant de migrer simultanément.

## Développement local

```bash
cp .env.example .env   # SECRET_KEY suffit pour commencer, le reste a des défauts dev-safe
docker compose up -d
docker compose logs -f web
```

L'app est sur `http://localhost:8000/api/...`, Flower sur `http://localhost:5555`, Postgres sur
`localhost:5432`, Redis sur `localhost:6379`. Le code est bind-monté (`.:/app`) : toute modif est
prise en compte immédiatement par `runserver`, pas besoin de rebuild sauf changement de
`requirements.txt`/`Dockerfile`.

```bash
docker compose exec web python manage.py createsuperuser
docker compose down          # -v pour aussi supprimer les données Postgres
```

## Production (VPS)

### 1. Premier lancement (HTTP, sans domaine)

```bash
cp .env.example .env
# Éditer .env : SECRET_KEY, POSTGRES_PASSWORD, et au minimum NOTCHPAY_*/TWILIO_* si tu veux
# tester ces flux (sinon ils restent inertes/mockables). Optionnel : DJANGO_SUPERUSER_PHONE_NUMBER
# + DJANGO_SUPERUSER_PASSWORD (+ _FIRST_NAME/_LAST_NAME) pour créer un superuser automatiquement
# au premier démarrage (entrypoint.sh, idempotent).

docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f web   # confirme migrate/collectstatic OK
```

À ce stade, `deploy/nginx/sentaa.conf` sert l'app en HTTP simple sur le port 80 — suffisant pour
tester depuis l'IP du VPS (`http://<ip-vps>/api/...`), sans certificat.

### 2. Passer en HTTPS une fois un nom de domaine pointé vers le VPS

1. Pointer le DNS (A record) du domaine vers l'IP du VPS.
2. Renseigner `DOMAIN_NAME=ton-domaine.com` dans `.env`.
3. Émettre le premier certificat (le bloc `/.well-known/acme-challenge/` de la config bootstrap
   suffit pour la validation webroot) :

   ```bash
   docker compose -f docker-compose.prod.yml run --rm certbot certonly --webroot \
     -w /var/www/certbot -d ton-domaine.com \
     --email toi@example.com --agree-tos --no-eff-email
   ```

4. Activer la config HTTPS :

   ```bash
   sed "s/DOMAIN_NAME_PLACEHOLDER/ton-domaine.com/g" \
     deploy/nginx/sentaa-ssl.conf.example > deploy/nginx/sentaa.conf
   docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
   ```

5. Passer `DEBUG=False` (si pas déjà fait) et `ALLOWED_HOSTS`/`CORS_ALLOWED_ORIGINS` sur le vrai
   domaine dans `.env`, puis `docker compose -f docker-compose.prod.yml up -d` pour relancer `web`
   avec la nouvelle config.

### 3. Renouvellement du certificat (cron sur l'hôte, pas dans le compose)

Ajouter à la crontab de l'hôte (`crontab -e`) :

```
0 3 * * * cd /chemin/vers/sentaaback && docker compose -f docker-compose.prod.yml run --rm certbot renew --quiet && docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

### 4. Monitoring des tâches Celery (Flower)

Flower n'est jamais exposé publiquement (`127.0.0.1:5555` uniquement, même règle en dev/prod).
Depuis ton poste, en prod :

```bash
ssh -L 5555:localhost:5555 <user>@<vps>
# puis ouvrir http://localhost:5555 en local
```

### 5. Prérequis avant test/prod (comptes externes, hors code)

- **NotchPay** : compte marchand (sandbox pour tester, puis prod) → `NOTCHPAY_PUBLIC_KEY`/`NOTCHPAY_PRIVATE_KEY`.
- **Twilio** : compte + numéro expéditeur validé pour le Cameroun → `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_FROM_NUMBER`, `SMS_BACKEND=twilio`.
- **Email SMTP** : un compte SMTP (Gmail, Mailgun, ton propre serveur...) → `EMAIL_HOST`/`EMAIL_PORT`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`. Sans ça, les emails restent en mode "console" (juste loggés, jamais envoyés).
- **Nom de domaine** pointé vers l'IP du VPS, pour le TLS.
- **AWS S3 (optionnel sur VPS)** : contrairement à Render, le disque du VPS est persistant — `media/` survit aux redéploiements sans S3. S3 ne devient utile que si tu veux un CDN ou scaler horizontalement sur plusieurs VPS plus tard.

### 6. Réglages de perf déjà en place

- `GUNICORN_WORKERS` (`.env`, défaut 3) — ajuste selon les vCPU du VPS (`(2 × vCPU) + 1` est un bon point de départ).
- `--max-requests`/`--max-requests-jitter` sur gunicorn et `--max-tasks-per-child` sur Celery : recyclage périodique des workers, évite les fuites mémoire en tourne longue.
- `CELERY_CONCURRENCY` (`.env`, défaut 4) — nombre de tâches Celery traitées en parallèle.
- nginx sert `/static/`/`/media/` directement (pas de passage par Python/whitenoise) et gère la compression gzip.
- Redis sert 3 rôles (cache, channel layer, broker Celery) sur des DB logiques séparées (`/0`, `/2`) — suffisant à cette échelle, pas besoin d'instances séparées.
