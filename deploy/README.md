# Déploiement Sentaa — VPS unique, staging + prod isolés

Architecture volontairement simple, 3 briques indépendantes (aucun registry, aucun réseau
Docker partagé entre stacks) :

```
                      ┌──────────────────────────────────────────┐
   Internet 443 ─────▶│  Gateway nginx (docker-compose.gateway)  │  TLS (Let's Encrypt)
                      │  network_mode: host                       │
                      └───────────┬──────────────────┬───────────┘
                     api.sentaa.net           dev.sentaa.net
                          │ 127.0.0.1:8001          │ 127.0.0.1:8002
              ┌───────────▼──────────┐   ┌──────────▼───────────┐
              │  Stack PROD          │   │  Stack STAGING       │
              │  docker-compose.prod │   │  docker-compose.stag.│
              │  nginx→web→celery… db │   │  nginx→web→celery… db │
              │  projet: sentaa-prod │   │  projet:sentaa-staging│
              └──────────────────────┘   └──────────────────────┘
```

1. **Gateway** (`docker-compose.gateway.yml`) — seul service sur `80/443`, termine le TLS,
   route chaque domaine vers le nginx interne du stack via la loopback. Tourne en
   `network_mode: host`. Config : `nginx/nginx.conf`.
2. **Stacks applicatifs** (`docker-compose.prod.yml` / `docker-compose.staging.yml`) — chacun
   complet et isolé (projet/volumes/réseaux séparés) : `db` + `redis` + `web` (Django ASGI) +
   `celery_worker` + `celery_beat` + `flower` + `nginx` interne. Le nginx interne sert
   `/static/` + `/media/` et proxifie le reste (WebSockets `/ws/` inclus). Configs :
   `nginx/nginx.prod.conf`, `nginx/nginx.staging.conf`.
3. **TLS** (`scripts/setup-ssl.sh` / `scripts/renew-ssl.sh`) — certbot sur l'hôte, 1 certificat
   SAN pour les deux domaines, renouvellement par cron.

Le CI/CD (`.github/workflows/deploy.yml`) build **sur le VPS** : `git reset --hard` puis
`docker compose build && up -d`. `dev` → staging, `main` → prod.

---

## Mise en place initiale du VPS (une seule fois)

Prérequis : Docker + Docker Compose v2, utilisateur non-root dans le groupe `docker`,
DNS `api.sentaa.net` **et** `dev.sentaa.net` pointant vers l'IP du VPS.

### 1. Cloner le repo dans les deux dossiers

```bash
sudo mkdir -p /var/www/sentaa-backend /var/www/sentaa-backend-dev
sudo chown "$USER" /var/www/sentaa-backend /var/www/sentaa-backend-dev

git clone <url-repo> /var/www/sentaa-backend        # prod
git -C /var/www/sentaa-backend checkout main

git clone <url-repo> /var/www/sentaa-backend-dev    # staging
git -C /var/www/sentaa-backend-dev checkout dev
```

> Le VPS doit pouvoir `git fetch` sans interaction (clé de déploiement / HTTPS avec token).

### 2. Fichier `.env` par environnement

```bash
cp /var/www/sentaa-backend/.env.example     /var/www/sentaa-backend/.env
cp /var/www/sentaa-backend-dev/.env.example /var/www/sentaa-backend-dev/.env
```

Éditer chaque `.env` : au minimum `SECRET_KEY`, `POSTGRES_PASSWORD`, `FLOWER_PASSWORD`, et les
clés externes voulues (`NOTCHPAY_*`, `TWILIO_*`, `CLOUDINARY_*`). `ALLOWED_HOSTS` est fixé par
le compose (pas besoin de le mettre). Ces `.env` ne sont **jamais** commités.

### 3. TLS + gateway (une seule fois)

```bash
cd /var/www/sentaa-backend
CERTBOT_EMAIL=contact@sentaa.net ./scripts/setup-ssl.sh
```

Émet le certificat SAN (api + dev), démarre la gateway, installe le cron de renouvellement.

### 4. Premier démarrage des stacks

```bash
cd /var/www/sentaa-backend     && docker compose -f docker-compose.prod.yml    up -d --build
cd /var/www/sentaa-backend-dev && docker compose -f docker-compose.staging.yml up -d --build
```

`web` joue automatiquement `migrate` + `collectstatic` (via `entrypoint.sh`, `RUN_MIGRATIONS=true`).

### 5. Secrets GitHub (Settings → Environments : `staging` et `prod`)

| Secret         | Description                                  |
|----------------|----------------------------------------------|
| `VPS_HOST`     | IP / hostname du VPS                         |
| `VPS_USER`     | utilisateur SSH (groupe `docker`)            |
| `VPS_SSH_KEY`  | clé privée SSH autorisée sur le VPS          |
| `VPS_PORT`     | port SSH (souvent `22`)                      |

Ensuite, tout push sur `dev`/`main` déclenche tests → build sur le VPS → `up -d`.

---

## Opérations courantes

```bash
# Logs
docker compose -f docker-compose.prod.yml logs -f web

# Superuser (ou via DJANGO_SUPERUSER_* dans .env au premier démarrage)
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser

# Monitoring Celery (Flower) — tunnel SSH (prod 5555, staging 5556)
ssh -L 5555:localhost:5555 <user>@<vps>   # puis http://localhost:5555

# Renouvellement TLS manuel (sinon cron)
./scripts/renew-ssl.sh
```

## Notes

- **Ports internes** `127.0.0.1:8001` (prod) / `127.0.0.1:8002` (staging) : loopback uniquement,
  invisibles d'internet ; seule la gateway (sur le réseau hôte) les atteint.
- **Réseau `backend` `internal: true`** : `db`/`redis` n'ont aucun accès internet.
- **Média** : servis par le nginx interne depuis le volume `media_volume`. Si Cloudinary/S3 est
  configuré, les fichiers passent par ces services et `/media/` local reste inutilisé (inoffensif).
- **CSRF admin** : si tu utilises l'admin Django derrière HTTPS, pense à ajouter
  `CSRF_TRUSTED_ORIGINS=https://api.sentaa.net,https://dev.sentaa.net` (settings) — non bloquant
  pour l'API JWT.
