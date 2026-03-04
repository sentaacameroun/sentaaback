# back_sentaa

API backend Django pour la plateforme Sentaa (marketplace, jobs, logistique, escrow). Le projet est structuré en plusieurs applications Django :

- `users` : gestion des comptes, authentification par numéro de téléphone (modèle `users.models.User`).
- `marketplace` : listings d'articles, catégories, recherche, vendu par des **sellers**.
- `jobs` : offres d'emploi et candidatures.
- `logistics` : (vide pour l'instant) réservée aux fonctionnalités de suivi/delivery.
- `escrow` : gestion des commandes et de l'entiercement des paiements.

## Architecture

- Monolithe Django avec DRF (Django REST Framework) exposant une API REST sous `/api/`.
- Authentification JWT via `rest_framework_simplejwt`.
- Schéma OpenAPI généré par `drf_spectacular` (endpoint `/schema/` à ajouter selon besoins).
- Base de données PostgreSQL (paramétrée via variables d'environnement `POSTGRES_*`), Redis pour cache.
- Clés primaires UUID pour la plupart des modèles.

## Prérequis

- Python 3.11+ (venv recommandé).
- PostgreSQL (ou sqlite pour développement local).
- Redis (optionnel, seul cache).

## Installation

```bash
# à la racine du projet
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# variables d'environnement (exemple avec sqlite)
export SECRET_KEY='devsecret'
export POSTGRES_DB='sentaa'
export POSTGRES_USER='sentaa'
export POSTGRES_PASS='sentaa'
export POSTGRES_HOST='localhost'
export POSTGRES_PORT='5432'

# ou, pour dev rapide, configurer sqlite dans back_sentaa/local_settings.py
```

## Commandes utiles

- `python manage.py migrate` — appliquer les migrations.
- `python manage.py createsuperuser` — créer un admin (veillez à fournir `phone_number`).
- `python manage.py runserver` — démarrer le serveur.
- `python manage.py test` — lancer la suite de tests unitaires.

## Développement app par app

- Chaque application est autonome et déclarée dans `INSTALLED_APPS`.
- Le routage se fait depuis `back_sentaa/urls.py` ; ajouter ou réparer `urls.py` dans chaque app.
- Les modèles utilisent des UUID ; préférer `django-uuid-upload-path` si besoin d'upload.

## Conventions

- Utiliser `phone_number` comme identifiant d'utilisateur.
- Respecter le pattern `is_<role>` (seller, recruiter, courier) dans `User` pour permissions simplifiées.
- Les tests se trouvent dans chaque app (`tests.py`).

## Tâches de maintenance courantes

- Ajouter ou mettre à jour `.env` (non commité).
- Surveiller `db.sqlite3` (généré en dev) et migrer vers PostgreSQL en production.
- Fusionner les changements de `local_settings.py` dans un exemple distribué.

## À venir

- Écrire des vues/serializers pour chaque app et définir des routes DRF.
- Compléter l'app `logistics` avec les endpoints requis.

---
*Ce README est généré automatiquement pour aider les nouveaux contributeurs. Pour des questions spécifiques, consultez les fichiers d'instruction de l'IA ou contactez le mainteneur.*
