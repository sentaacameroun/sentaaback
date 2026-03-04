# Instructions Copilot pour le projet back_sentaa

Objectif : fournir à un agent IA les informations essentielles pour être immédiatement productif.

- Contexte général : projet Django monolithique composé des apps `jobs`, `marketplace`, `logistics`, `escrow`, `users`.
- API REST : utilisation de Django REST Framework + `rest_framework_simplejwt` pour JWT. Le schéma OpenAPI est géré par `drf_spectacular`.
- Authentification : modèle d'utilisateur personnalisé `users.models.User` (auth par `phone_number`). Références : `users/models.py`.

Points d'architecture importants
- `back_sentaa/settings.py` : centralise la config et lit les variables d'environnement (SECRET_KEY, POSTGRES_*). Par défaut la base est configurée pour PostgreSQL via env vars.
- `AUTH_USER_MODEL = 'users.User'` : toute modification touchera la table d'auth.
- Caching : configuration Redis via `REDIS_URL` (variable env).
- Routes : `back_sentaa/urls.py` inclut `marketplace` et `logistics` sous `/api/` — remarquer que `marketplace/urls.py` est absent, donc le routage peut échouer si on démarre sans ajustement.

Workflows développeur essentiels (découverts dans le repo)
- Installer dépendances : `pip install -r requirements.txt` (utiliser un venv).
- Virtualenv possible fourni dans `sentaa/` (vérifier si utilisable). Sinon :
  - `python3 -m venv .venv && source .venv/bin/activate`
  - `pip install -r requirements.txt`
- Variables d'environnement : exporter au minimum `SECRET_KEY` et soit les `POSTGRES_*` soit adapter `DATABASES` pour sqlite en développement.
- Commandes courantes :
  - `python manage.py migrate`
  - `python manage.py createsuperuser`
  - `python manage.py runserver`
  - `python manage.py test`

Conventions et patterns spécifiques au projet
- Auth par numéro de téléphone : `users.models.User` utilise `PhoneNumberField` (package `phonenumber_field`). Cherchez cette dépendance si authentication/validation plantent.
- UUID primary keys : la plupart des modèles utilisent `UUIDField` comme `id` (ex : `marketplace.models.Listing`, `jobs.models.JobOffer`).
- Indexes et recherches : `Listing` définit un index combiné (`created_at`, `price`) — privilégiez les filtres/ordres correspondants pour les performances.
- Permissions : DRF global par défaut `IsAuthenticated` dans `REST_FRAMEWORK` ; les vues publiques doivent explicitement définir `permission_classes`.

Intégrations et points de friction connus
- Base de données : `settings.py` attend PostgreSQL par env vars — repo contient `db.sqlite3` (probablement de développement). Agents : vérifier `local_settings.py` pour overrides (actuellement vide).
- Routes manquantes : `back_sentaa/urls.py` inclut `marketplace.urls` mais `marketplace/urls.py` n'existe pas — corriger en ajoutant un module `urls.py` ou en modifiant `back_sentaa/urls.py`.
- REQUIRED_FIELDS dans `users.models.User` mentionne `full_name` alors que le modèle expose `first_name`/`last_name` — vérifier/ajuster avant de créer superuser.

Tâches recommandées pour un premier commit d'agent
- Ajouter un `README.md` racine (si absent) avec commandes d'exécution rapide.
- Fournir un `back_sentaa/local_settings.py` d'exemple (non suivi) qui force `sqlite3` pour dev et charge `SECRET_KEY` depuis `.env`.
- Créer `marketplace/urls.py` minimal ou retirer son include de `back_sentaa/urls.py`.

Fichiers à examiner en priorité
- `back_sentaa/settings.py` (env, DB, cache, REST_FRAMEWORK)
- `users/models.py` (custom user, phone auth, REQUIRED_FIELDS)
- `back_sentaa/urls.py` (inclusions d'API)
- `marketplace/models.py`, `escrow/models.py`, `jobs/models.py` (schémas DB et relations entre apps)

Questions pour le mainteneur (si le fonctionnement n'est pas clair)
- Préférez-vous PostgreSQL en local, ou est-ce acceptable d'ajouter un override sqlite pour le dev ?
- Voulez-vous que l'agent crée un `local_settings.py` d'exemple et un `marketplace/urls.py` minimal ?

Merci — dites-moi si vous voulez que je crée directement `back_sentaa/local_settings.py` et `marketplace/urls.py` exemples.
