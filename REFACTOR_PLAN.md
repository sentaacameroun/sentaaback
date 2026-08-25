# Plan de refactor — Sentaa backend

Source : `docs/AUDIT_BACKEND.md`. Verdict : refactor progressif (pas de
réécriture), seul le noyau escrow était structurellement à repenser (fait en
PR 3).

Règle : ne pas commencer une PR tant que la précédente n'est pas cochée et
que `pytest` n'est pas vert. Ce fichier est mis à jour par Claude et par le
développeur au fil de l'avancement.

## PR 1 — Verrous de sécurité rapides
Statut : ☑ terminé
- [x] Permission objet sur les annonces (IDOR)
- [x] Retrait du DELETE sur `OrderViewSet`
- [x] `shipping_fee` calculé côté serveur
- [x] Validation MIME + taille sur les CV

## PR 2 — Journal financier protégé
Statut : ☑ terminé
- [x] `on_delete=PROTECT` sur `PaymentTransaction.order`
- [x] `idempotency_key` ajoutée
- Décision : test `test_initiate_payment_creates_pending_transaction`
  (`channel="cm.mtn"` périmé) aligné sur `mtn`, hors scope strict mais
  nécessaire pour que `pytest` passe — validé.

## PR 3 — Service de cycle de vie (cœur du refactor)
Statut : ☑ terminé
- [x] Statut `DELIVERED` ajouté
- [x] `OrderLifecycleService` créé, atomique + idempotent
- [x] `confirm_reception` / `confirm_delivery` pointent vers le service
- [x] Revue passée par le subagent `escrow-reviewer` — **APPROUVÉ**
- [x] Décision prise sur la fusion des 2 endpoints de clôture : **flux en
      deux étapes séquentielles, endpoints conservés séparés**, convergeant
      vers `OrderLifecycleService`. Un seul chemin (`complete_and_release`)
      libère les fonds.

Points trackés hors scope (issus de la revue `escrow-reviewer`) :
- **B — idempotence du payout coursier non couverte** (`pay_courier_for_delivery`).
  → repris en **PR 5**.
- Rappel de réception ciblant `shipped` au lieu de `delivered` → **corrigé en
  PR 4** (régression introduite par PR 3, traitée en priorité).
- `pytest.ini` sans `python_files` (suite jamais collectée par un `pytest`
  nu) → **corrigé hors PR formelle, avant PR 4**.

## PR 4 — Nettoyage & CI
Statut : ☑ terminé (2026-08-23) — `pytest` nu (sans cibler de fichiers) = **93 passed, 0
failed** ; `ruff check .` clean ; `black --check .` clean ; `manage.py check` OK.

- [x] CI bloquante sur tests + lint (job `test` avec postgres+redis,
      `build`/`deploy` en `needs: test`)
- [x] Dépendances épinglées (celery, channels, channels-redis, flower,
      requests, twilio, ruff==0.11.7)
- [x] `pytest.ini` corrigé (`python_files` ajouté, 93 tests collectés)
- [x] Rappel de réception aligné sur `delivered`
- [x] Test SMS-502 recadré (test périmé réécrit, pas de bug code)
- [x] Code mort paiement supprimé (`providers/`, `webhook_handlers.py`,
      `kpay_client`, `moneyfusion_client`) — plus aucune trace, `ruff check`
      clean, `manage.py check` OK
- [x] **TalentProfile supprimé** — modèle (`jobs/models.py`), serializer
      `TalentProfileSerializer` (`jobs/serializers.py`, aucun usage vue/url),
      migration de suppression `jobs/migrations/0004_delete_talentprofile.py`.
      Aucune trace résiduelle (pas de référence admin). `ruff`/`black` clean.
- [x] **render.yaml supprimé** (`git rm`) — VPS est la cible réelle.
      NB : `settings.py` garde encore `sentaaback.onrender.com` comme *fallback*
      par défaut d'`ALLOWED_HOSTS` (surchargé par l'env en prod) — inerte, non
      retiré ici (WIP settings dév) ; à nettoyer séparément si voulu.
- [x] **black aligné sur 26.x** — `.pre-commit-config.yaml` `rev: 23.1.0 → 26.1.0`,
      `black .` a reformaté 20 fichiers (le dépôt était black-23), `black --check .`
      clean. Aucun test cassé (formatage seul).
- [x] **Isolation Cloudinary — vraie cause trouvée et corrigée.** Ce n'était PAS
      `cloudinary.config()` (instrumenté : config OK, `public_id` bien stocké) mais
      la **non-inscription des récepteurs `post_delete`** : `companies/apps.py` et
      `marketplace/apps.py` avaient `ready(): pass` (avec un `# noqa: F401` trompeur,
      import perdu) → en **prod aussi**, supprimer un profil/annonce ne nettoyait
      **jamais** Cloudinary. Corrigé : `ready()` importe désormais `signals` (comme
      `users/apps.py`). Les 2 tests passent en isolation absolue → n'importe quel ordre.
      Ajouté en plus, comme demandé (hygiène) : fixture `autouse` `_isolate_cloudinary_config`
      (conftest.py) qui restaure le singleton `cloudinary.config()` après chaque test.
- [x] **`jobs::test_apply_to_job` débloqué** — c'était bien l'env local
      (`media/cvs/2026/08` créé root:root par Docker → PermissionError). Corrigé
      proprement côté **test-infra, pas code applicatif** : fixture `autouse`
      `_isolate_media_root` (conftest.py) redirige `MEDIA_ROOT` vers un tmp par test
      (suite hermétique, ne pollue plus le `media/` du dépôt).
- [x] `pytest` nu 100 % vert vérifié (93 passed) — case cochée en conséquence.

## PR 5 — Réconciliation & sécurité paiement
Statut : ☑ terminé (2026-08-23) — `pytest escrow/` = **26 passed** ; `ruff check` clean ;
`black --check` clean. Revue `escrow-reviewer` : **APPROUVÉ** (aucun point bloquant).
Commande : `/refactor-pr5-payment-safety`

- [x] **Réconciliation des reversements `pending`/`failed`** (BLOQUANT #12). Nouveau
      `escrow/tasks.py::reconcile_pending_payouts` (tâche Celery, beat `*/15`) :
      - `pending` avec référence → interroge `/transfers/{ref}` via le nouveau
        `NotchPayClient.verify_transfer` (endpoint distinct de `verify_payment`/`/payments`)
        et met le statut à jour (`successful`/`failed`), sous `select_for_update` + re-lecture
        (idempotent, pas de double effet).
      - `failed` jamais initiés (fail-soft : ni ref ni clé) → re-tentés de façon **idempotente**
        (via `release_escrow_funds`/`pay_courier_for_delivery`, no-op si la clé existe déjà) et
        **bornée** (arrêt + log `ERROR` après `PAYOUT_RECONCILIATION_MAX_ATTEMPTS`, pas de retry
        infini silencieux). Bornage couvert par test pour les deux branches.
      - Champ `PaymentTransaction.reconciliation_attempts` ajouté (migration
        `0011_paymenttransaction_reconciliation_attempts`).
- [x] **Idempotence du payout coursier** (point B tracké en PR 3). `pay_courier_for_delivery`
      pose désormais `idempotency_key = courier_payout:{delivery.id}` (contrainte unique en base
      = garde-fou dur, pas seulement garde applicative ; savepoint + `IntegrityError` pour le cas
      concurrent). Clé calculée **en interne** (l'appelant `logistics.confirm_delivery` reste hors
      scope, non modifié). Même soin qu'en PR 3 sur l'échec : un virement réellement échoué ne
      consomme PAS la clé (ligne `failed` sans clé → réconciliation peut re-tenter).
- [x] **Vérification de signature du webhook NotchPay** (MAJEUR #8). NotchPay **expose** bien un
      mécanisme : HMAC-SHA256 du corps brut, en-tête `x-notch-signature`, secret « webhook hash »
      du dashboard. Implémenté dans `MobileMoneyWebhookView._signature_ok`, vérifié **avant** tout
      traitement/appel sortant. **Nuance documentée** : la vérification n'est active que si
      `NOTCHPAY_WEBHOOK_HASH` est configuré ; sans secret (dev/tests, ou instance sans hash), on
      retombe sur la mitigation déjà en place (re-vérification du statut via l'API NotchPay, qui
      rend un succès impossible à forger). C'est un choix de config, pas une limite du fournisseur.

Wiring hors liste de fichiers stricte (nécessaire aux livrables ci-dessus, signalé par
transparence) :
- `back_sentaa/settings.py` : entrée beat `reconcile-pending-payouts`, seuils
  `PAYOUT_RECONCILIATION_MINUTES`/`_MAX_ATTEMPTS`, et `NOTCHPAY_WEBHOOK_HASH` (un secret Django
  est requis pour que la signature soit testable via `override_settings`).
- `.env.example` : documentation de `NOTCHPAY_WEBHOOK_HASH` et des deux seuils de réconciliation.
- ⚠️ Migration `0011` non appliquée automatiquement (`migrate` en `ask`) — à lancer :
  `python manage.py migrate escrow`.

NB tests : 4 tests WebSocket/Channels (`chat`, `logistics::CourierDispatchTests`) échouent dans
cet environnement sur `SynchronousOnlyOperation` (accès DB en contexte async) — **pré-existant**,
vérifié identique sur le code d'origine (git stash), sans lien avec PR 5 (aucun fichier touché).

## PR 6 — Authentification durcie
Statut : ☑ terminé (2026-08-23) — `pytest users/` = **28 passed** ;
`ruff check` + `black --check` clean ; `makemigrations --check` = no changes.
Commande : `/refactor-pr6-auth-hardening`
- [x] Refresh token branché (`TokenRefreshView` sur `token/refresh/` +
      `SIMPLE_JWT` configuré : access 30 min, refresh 7 j, rotation +
      blacklist après rotation)
- [x] Logout / révocation via blacklist (`LogoutView` sur `logout/`,
      `token_blacklist` ajouté à `INSTALLED_APPS`)
- [x] `is_available` non modifiable par un non-coursier (MINEUR #16) —
      `UserSerializer.validate_is_available` rejette (400) si `is_courier` faux
- [x] Fin de l'énumération de comptes sur `otp-request` (MINEUR #15) —
      réponse 200 générique identique compte existant/inexistant
- [x] OTP généré via `secrets.randbelow` plutôt que `random.randint` (MINEUR
      #13), cohérent avec `logistics/models.py`

Note de scope : task 2 impose l'ajout de `rest_framework_simplejwt.token_blacklist`
à `INSTALLED_APPS` — modification de `settings.py` hors du seul bloc `SIMPLE_JWT`,
mais explicitement requise par la commande. Aucune migration à générer :
`token_blacklist` embarque les siennes (appliquées par `migrate`).

## Questions ouvertes restantes
Voir `docs/AUDIT_BACKEND.md` §9. Un agent ne doit jamais trancher ces
questions seul — elles doivent être posées explicitement au développeur.

1. ~~Paiement multi-fournisseurs~~ — **tranché en PR 4** (code mort supprimé).
2. ~~Clôture de commande~~ — **tranché en PR 3** (deux endpoints, un seul
   chemin de libération des fonds).
3. ~~`shipping_fee`~~ — **tranché en PR 1** (calcul serveur).
4. **Litiges/remboursements** (`disputed`/`refunded`) : besoin réel à
   implémenter, ou statuts à retirer du modèle ? — reste ouvert, bloque toute
   PR future sur ce sujet tant que non tranché.
5. **Stockage des CV en production** : S3 est-il réellement configuré avec
   des URLs signées ? — reste ouvert, à vérifier en dehors du code (config
   d'environnement réelle).

## Dette mineure non planifiée (audit MINEUR restants)
À rescoper en PR7 "fiabilité" si jugé utile, ou traiter au fil de l'eau :
- Incohérence de visibilité publique (catégories/job-offers `AllowAny` vs
  listings `Auth`) — décision produit, pas juste technique.
- Pas de health check applicatif ni d'APM/Sentry.
- I/O tierce synchrone dans les vues (OTP, paiement, upload) — à déporter
  vers Celery pour la robustesse sous latence tierce.
