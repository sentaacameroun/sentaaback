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

## PR 7 — Intégration KPay & architecture multi-provider
Statut : ☑ terminé (2026-09-02) — `pytest` nu = **180 passed** ; `ruff check .` clean ;
`black --check` clean (fichiers touchés) ; `manage.py makemigrations --check` = no changes ;
revue `escrow-reviewer` demandée sur ce diff (voir verdict avant de cocher définitivement en
CI/déploiement).

Demande directe du développeur (hors cycle `/refactor-prX`, pas de commande dédiée) : les
webhooks NotchPay en prod étaient peu fiables (voir commits `cc52484`/`1abd301`, forme du
payload webhook corrigée mais tests jamais réalignés — 4 tests `escrow/tests.py` étaient
rouges avant cette PR, corrigés ici) ; KPay (https://kpay.site/documentation) devient
l'intégrateur **principal**, NotchPay est conservé comme fournisseur secondaire, sous une
véritable architecture multi-provider (ce que PR 4 avait tranché en sens inverse en
supprimant le code mort `providers/`/`kpay_client`/`moneyfusion_client` — voir question 1
ci-dessous, rouverte).

- [x] **`escrow/services/providers/`** : interface commune `PaymentProviderClient` (ABC),
      résultat normalisé `ProviderResult` (statuts `pending`/`successful`/`failed`, alignés
      sur `PaymentTransaction.STATUS_CHOICES`), exception commune `PaymentProviderError`.
      `NotchPayClient` déplacé depuis `escrow/services/notchpay_client.py` (comportement HTTP
      inchangé, seule la sortie est désormais normalisée). Nouveau `KPayClient` (auth
      `X-API-Key`/`X-Secret-Key`, `/api/v1/payments/init`+`/{id}`,
      `/api/v1/payments/withdraw`+`/{id}`, mapping canal `mtn`/`orange` →
      `MTN_MOMO_CMR`/`ORANGE_CMR` — Sentaa n'opère qu'au Cameroun).
- [x] **Sélection par variable d'env** : `PAYMENT_PROVIDER` (`settings.py`, défaut `"kpay"`),
      factory `get_payment_client(provider_name=None)`. `escrow/views.py`
      (`initiate_payment`), `escrow/services/payouts.py` (`release_escrow_funds`,
      `pay_courier_for_delivery`) l'utilisent désormais au lieu d'instancier `NotchPayClient()`
      en dur.
- [x] **`PaymentTransaction.provider` enfin peuplé** (le champ existait déjà, choix
      `notchpay`/`kpay`/`moneyfusion`, mais n'était jamais écrit) — chaque transaction garde
      le fournisseur qui l'a réellement traitée. Ajouté à `escrow/admin.py`
      (list_display/list_filter).
- [x] **Réconciliation provider-aware** (`escrow/tasks.py::_reconcile_pending`) :
      `get_payment_client(txn.provider or None)` appelé PAR TRANSACTION dans la boucle (pas un
      seul client partagé pour tout le batch) — sinon changer `PAYMENT_PROVIDER` casserait la
      réconciliation des reversements déjà en cours chez l'ancien fournisseur (interrogerait
      l'API KPay avec une référence NotchPay, ou l'inverse). Ligne `provider=""` (legacy,
      avant peuplement du champ) retombe sur le fournisseur actif. Testé explicitement
      (`PaymentReconciliationMultiProviderTests`).
- [x] **Webhook KPay** (`/kpay-webhook/`, `KPayWebhookView`) : signature HMAC-SHA256
      (`X-KPAY-Signature` / `KPAY_WEBHOOK_SECRET`, même politique de repli que NotchPay si
      secret absent). Ne traite que les événements `payment.*` — les événements `payout.*`/
      `refund.*` (KPay en envoie, contrairement à NotchPay) ne sont **pas** consommés ici,
      volontairement : la convergence de statut des reversements reste assurée exclusivement
      par la réconciliation Celery (`escrow/tasks.py`), pour ne pas dupliquer le chemin
      d'écriture des statuts de payout. **Suivi possible** si la latence de 15 min de la
      réconciliation devient un problème produit : consommer aussi `payout.completed/failed`
      côté webhook, en le faisant converger vers le même code que la réconciliation (jamais un
      second chemin distinct).
- [x] **`_CollectWebhookView`** : base commune factorisant la clôture webhook (re-vérification
      du statut auprès du fournisseur, puis transition `paid_escrow`) entre
      `MobileMoneyWebhookView` (NotchPay) et `KPayWebhookView` — un seul endroit écrit cette
      transition depuis un webhook, quel que soit le fournisseur. Chaque vue instancie
      **explicitement** son propre client (`NotchPayClient()`/`KPayClient()`), jamais via
      `get_payment_client()` : un webhook est par construction propre à UN fournisseur (URL de
      callback configurée côté dashboard fournisseur), indépendamment de `PAYMENT_PROVIDER`.
- [x] **Idempotence KPay en plus de la contrainte unique existante (règle #2)** :
      `initialize_transfer` reçoit désormais la clé d'idempotence Senta'a
      (`release:{order.id}`/`courier_payout:{delivery.id}`) et la transmet en `externalId` à
      KPay quand elle est fournie (`NotchPayClient` accepte le même paramètre mais ne l'utilise
      pas encore côté payload — comportement NotchPay volontairement inchangé, hors scope).
- [x] **Faille croisée entre fournisseurs corrigée** (trouvée par la revue `escrow-reviewer`
      sur ce diff). `_CollectWebhookView._process` rejette désormais (log + 200 idempotent,
      sans appeler le fournisseur) tout webhook dont `txn.provider` est renseigné et diffère
      du fournisseur de l'endpoint appelé (`client.name`) — sans ce garde-fou, une référence
      provider valide chez KPay pouvait cibler une transaction traitée par NotchPay (et
      inversement). `txn.provider` vide (lignes créées avant l'ajout du champ) ne bloque pas.
      Testé (`test_webhook_ignores_transaction_owned_by_another_provider`, sur les deux
      endpoints).
- [x] **Faille de rejeu inter-transactions corrigée** (même revue, point plus sérieux —
      voir l'ancienne section "⚠️ non corrigée" ci-dessous, résolue depuis). Le vrai problème :
      `_process` faisait confiance à `provider_reference` tel que fourni PAR LE CORPS de la
      requête webhook (payload non authentifié fonctionnellement, endpoint `AllowAny`), sans
      jamais vérifier qu'il correspondait à la transaction visée par `reference` — un
      attaquant pouvait rejouer le `provider_reference` d'un paiement RÉEL et complété chez
      lui (même minime, même sur une autre commande — potentiellement la sienne, pas besoin de
      cibler un tiers) contre le `reference` d'une commande bien plus chère, jamais payée, et
      la faire créditer `paid_escrow`. Piste initialement envisagée (comparer le champ que
      le fournisseur associe lui-même à `provider_reference` dans sa réponse `verify_payment`)
      abandonnée : deux sources sur le format exact de la réponse NotchPay se contredisaient,
      et un mauvais nom de champ aurait rejeté silencieusement tous les paiements réels.
      **Solution retenue (bien plus robuste, suggérée par le développeur)** : stocker
      `provider_reference` (celle attribuée par le fournisseur À LA CRÉATION du paiement,
      `initiate_payment` — déjà extraite par les clients mais jusqu'ici jamais persistée,
      champ `PaymentTransaction.provider_reference` existant mais mort) puis, dans `_process`,
      comparer AVANT tout appel réseau le `provider_reference` reçu dans le webhook à celui
      stocké à la création. Ne dépend d'aucune connaissance du format de réponse d'un
      fournisseur tiers — uniquement de données que Senta'a contrôle. Vide (transactions
      créées avant ce correctif) ne bloque pas, la re-vérification live reste alors la seule
      mitigation. Testé (`test_webhook_rejects_replayed_provider_reference` +
      `test_webhook_accepts_matching_provider_reference`, sur les deux endpoints).
- [x] **Réponse `initiate_payment` réduite** (à l'origine de la découverte du point
      ci-dessus) : ne renvoie plus `result.raw` (objet brut du fournisseur, pouvait exposer
      des identifiants internes) au frontend — seulement `reference`, `status`, `payment_flow`
      et `checkout_url` quand il existe.
- [x] **`payment_flow` explicite** (suite à une question directe du développeur : "comment le
      front va savoir qu'il faut ouvrir un lien ou gérer l'USSD ?"). Avec deux fournisseurs
      aux comportements différents (NotchPay : toujours une page hébergée puisqu'aucun canal
      n'est envoyé dans le payload de création ; KPay : toujours un push USSD direct puisque
      le canal est toujours fourni), deviner le flux à partir de la seule présence de
      `checkout_url` aurait été implicite et fragile face à un futur 3e mode. Ajout de
      `ProviderResult.payment_flow` (`FLOW_REDIRECT`/`FLOW_USSD`,
      `escrow/services/providers/base.py`), toujours présent dans la réponse
      `initiate_payment` — c'est ce champ que le frontend doit tester, jamais la présence de
      `checkout_url`. Champ NotchPay confirmé par le développeur : `transaction
      .authorization_url` (pas `checkout_url` comme supposé initialement — corrigé dans
      `notchpay.py`).
- [x] **Bug webhook NotchPay corrigé dans les tests** (pas dans le code métier, déjà bon sur
      `dev`) : 4 tests (`test_webhook_marks_order_paid_on_success`,
      `test_webhook_ignores_unconfirmed_status`, `test_webhook_is_idempotent_on_duplicate_calls`,
      `test_webhook_accepts_valid_signature`) postaient un payload à plat
      (`{"reference": ...}`) alors que la vue (corrigée par `cc52484`/`1abd301`, avant cette
      PR) attend la forme réelle NotchPay, imbriquée sous `"data"`. Réalignés sur la vraie
      forme du payload.

Fichiers touchés hors du périmètre `escrow/services/providers/` (nécessaires, signalés par
transparence) : `escrow/views.py`, `escrow/urls.py`, `escrow/services/payouts.py`,
`escrow/tasks.py`, `escrow/admin.py`, `back_sentaa/settings.py`, `.env.example`,
`escrow/tests.py`, `logistics/tests.py` (patch targets déplacés vers
`escrow.services.providers.notchpay.NotchPayClient`, mocks renvoyant désormais des
`ProviderResult` au lieu de dicts bruts — contrat normalisé — `@override_settings
(PAYMENT_PROVIDER="notchpay")` ajouté aux classes historiques pour rester des tests de
régression NotchPay explicites).

Non fait, volontairement hors scope :
- `MoneyFusionClient` (3e choix déjà présent dans `PaymentTransaction.PROVIDERS`) : pas
  demandé, pas implémenté — le choix `"moneyfusion"` reste un slot inutilisé.
- Reformatage du numéro de téléphone par fournisseur (KPay documente `"237653456789"` sans
  `+` ; le code existant passe `phone_number`/`str(user.phone_number)` tel quel, sans
  transformation, y compris pour NotchPay déjà en prod) — non touché, comportement identique
  à l'existant.

**Revue de suivi (`escrow-reviewer`) sur ce correctif de rejeu : APPROUVÉ.** Vérifié
spécifiquement : le garde-fou ferme bien le scénario décrit (aucun contournement trouvé, y
compris via une tentative d'auto-initiation sur la commande cible — bloquée en amont par la
permission objet existante) ; il est bien placé avant tout appel réseau ; les tests ajoutés
échoueraient effectivement sans le correctif (vérifié) ; aucun usage existant du repo ne
dépend de l'ancienne forme de réponse `initiate_payment`. Point mineur relevé et corrigé dans
la foulée : `initiate_payment` loggue désormais un `warning` explicite quand la réponse
fournisseur ne contient pas de `provider_reference` (dégradation silencieuse du garde-fou
sinon, pour cette transaction précise) — testé
(`test_initiate_payment_logs_warning_when_provider_reference_missing`).

### Mode GATEWAY KPay (page hébergée avec choix cartes/PayPal)

Demande directe du développeur : le frontend doit pouvoir choisir, même quand KPay est le
fournisseur actif, un paiement par page hébergée (mode GATEWAY KPay) plutôt que le push USSD
direct — pour que les cartes bancaires (Visa/Mastercard) et PayPal apparaissent en plus des
choix Mobile Money, comme documenté sur https://kpay.site/documentation/cartes-paypal.

- [x] `InitiatePaymentSerializer` : `phone_number`/`channel` deviennent optionnels — fournis
      ensemble → paiement direct (push USSD chez KPay) ; omis ensemble → mode page hébergée,
      `return_url` devient alors obligatoire (fournie par le **frontend**, décision explicite
      du développeur : deep link app ou page web, propre à son schéma — pas d'URL fixe
      côté backend). Combinaison partielle (l'un sans l'autre) rejetée (400).
- [x] `KPayClient.initialize_payment` : `channel` fourni → mode USSD inchangé (`provider` +
      `phoneNumber`) ; `channel` absent → mode GATEWAY (`returnUrl`/`cancelUrl` à la place,
      garde-fou `KPayError` si `return_url` manquant — défense en profondeur, la validation
      normale se fait dans le serializer). Réponse GATEWAY : `gatewayUrl` repris comme
      `checkout_url` (nom de champ confirmé via https://kpay.site/documentation/paiements et
      /cartes-paypal, cohérents entre eux).
- [x] `NotchPayClient.initialize_payment` : accepte les mêmes paramètres pour la conformité
      d'interface (toujours ignorés — un seul mode, déjà page hébergée) ; `phone` devient
      optionnel côté payload (`customer` omis si absent, au lieu d'envoyer une valeur nulle
      non vérifiée).
- [x] **Repli de canal pour le reversement** (bug trouvé en creusant cette fonctionnalité,
      pas signalé par le développeur) : `release_escrow_funds`/`pay_courier_for_delivery`
      (`escrow/services/payouts.py`) réutilisaient `last_collect.channel` tel quel pour le
      reversement — un `channel` vide (collecte par carte/PayPal, sans opérateur Mobile Money)
      aurait fait échouer *indéfiniment* le reversement chez KPay (`KeyError` →
      `KPayError` à chaque tentative de réconciliation, jamais résolu). Repli sur le même
      défaut que l'absence totale de collecte (`PaymentTransaction.CHANNELS[0][0]`) — un choix
      arbitraire assumé (aucun moyen fiable de connaître l'opérateur réel du vendeur pour un
      acheteur ayant payé par carte), documenté en code ; rejoint le circuit existant de
      reversements bornés + intervention manuelle en cas d'échec, pas une nouvelle classe de
      bug. Testé (`test_release_escrow_funds_falls_back_when_collect_channel_blank`,
      `test_pay_courier_falls_back_when_collect_channel_blank`).
- [x] `ProviderResult`/réponse `initiate_payment` : nouveau champ `payment_flow`
      (`FLOW_REDIRECT`/`FLOW_USSD`) déjà couvert plus haut, réutilisé ici pour le mode GATEWAY.

Non fait, volontairement hors scope : NotchPay n'a pas de second mode dans ce repo (toujours
page hébergée) — aucune tentative de lui ajouter un mode direct/USSD, non demandé.

## PR 8 — Idempotence des collectes (reprise de paiement)
Statut : ☑ terminé (2026-09-03) — `pytest` nu = **210 passed** ; `ruff check .` clean ;
`black --check .` clean (fichiers touchés) ; `manage.py makemigrations --check --dry-run` = no
changes. Revue `escrow-reviewer` (2 passages) : **APPROUVÉ**, aucun point bloquant.

Origine : question directe du développeur (hors cycle `/refactor-prX`, pas de commande
dédiée) en observant que l'idempotence de `PaymentTransaction` (`idempotency_key`, PR 2/PR 3)
ne couvre que les **sorties** de fonds (`release`, `courier_payout`) — jamais la collecte
(paiement acheteur). Rien n'empêchait `initiate_payment` d'être rappelé plusieurs fois pour la
même commande (retry frontend, double-clic, utilisateur qui a perdu le fil d'un paiement déjà
commencé et revient le reprendre) : chaque appel ouvrait une nouvelle session fournisseur et
créait une nouvelle `PaymentTransaction` `collect` `pending`, sans jamais réutiliser ni clôturer
les précédentes. Aucune reprise possible, transactions `pending` orphelines jamais réconciliées
(`reconcile_pending_payouts` ne couvrait que `withdraw`/`courier_payout`), et un risque latent
de double crédit si deux sessions concurrentes étaient toutes deux menées à terme côté
acheteur.

- [x] **Contrainte unique partielle** `unique_pending_collect_per_order`
      (`escrow/models.py`, migration `0012`) : au plus une collecte `pending` à la fois par
      commande, garde-fou DUR (règle #2, `.claude/rules/escrow-core.md`), pas une simple garde
      applicative. Index partiel (condition sur `status="pending"` ET
      `transaction_type="collect"`) plutôt qu'une `idempotency_key` fixe comme `release:
      {order.id}` : contrairement à un release, une collecte doit pouvoir être retentée une
      fois l'ancienne résolue (`successful`/`failed`) — le slot se libère alors
      automatiquement. Testé (`test_unique_pending_collect_constraint_rejects_duplicate`).
- [x] **`initiate_payment` réécrit** (`escrow/views.py`) :
      - Reprise : une collecte `pending` récente (< `COLLECT_RECONCILIATION_MINUTES`) est
        renvoyée telle quelle plutôt que d'en ouvrir une seconde.
      - Slot réservé **avant** tout appel fournisseur (`_reserve_collect_slot`, INSERT en
        savepoint + `except IntegrityError`), puis complété (`provider`/`provider_reference`/
        `payment_flow`/`checkout_url`) une fois l'appel résolu — **pas** l'inverse. Correctif
        trouvé par `escrow-reviewer` sur la 1ère version de cette PR : appeler le fournisseur
        avant de réserver le slot laissait deux requêtes concurrentes déclencher chacune un
        VRAI appel fournisseur (ex. deux push USSD au même acheteur) avant que la contrainte
        ne tranche seulement l'écriture — la session perdante, jamais persistée, devenait un
        paiement fantôme si l'acheteur la complétait quand même (webhook sans transaction à
        créditer). Sur échec fournisseur, la ligne réservée est explicitement clôturée
        `failed` (sinon invisible à la réconciliation, qui exclut les lignes sans
        `provider_reference`).
      - Une collecte `pending` expirée localement est **revérifiée auprès du fournisseur**
        avant d'être clôturée (`_resolve_stale_pending`), jamais présumée `failed` sans
        confirmation — 2e correctif `escrow-reviewer` : les webhooks NotchPay sont documentés
        peu fiables en prod (PR 7) ; déclarer `failed` un paiement en réalité réussi aurait
        fait ignorer silencieusement le webhook tardif correspondant. Trois issues gérées :
        `successful` (commande créditée, pas de nouvelle session), `pending` (réutilisée),
        `failed` (slot libéré).
      - Bug latent corrigé au passage : `reference` était générée via
        `int(timezone.now().timestamp())` (précision à la seconde) — deux appels à
        `initiate_payment` pour la même commande dans la même seconde généraient la même
        `reference`, en collision sur `PaymentTransaction.external_ref` (unique). Révélé en
        écrivant le test de remplacement d'une pending expirée. Remplacé par un suffixe
        `uuid4`.
- [x] **`escrow/services/collect.py`** (nouveau) : `apply_verified_collect_result(txn,
      verified)` factorise la transition `pending -> successful/failed` (+ `paid_escrow` sur
      succès) — point d'entrée unique partagé par `_CollectWebhookView._process` **et** la
      nouvelle tâche de réconciliation, pour ne jamais dupliquer cette logique (même principe
      que la règle #3 de `.claude/rules/escrow-core.md`, appliqué ici à `paid_escrow` plutôt
      qu'à la clôture de commande).
- [x] **`GET /orders/{id}/pending_payment/`** (`escrow/views.py`) : lecture seule de la
      collecte `pending` en cours pour une commande, sans déclencher d'appel fournisseur —
      permet au frontend de retrouver une session déjà commencée. Permission objet identique à
      `initiate_payment` (buyer uniquement). 404 si rien en cours.
- [x] **`reconcile_pending_collects`** (`escrow/tasks.py`, beat `*/10`) : symétrique de
      `reconcile_pending_payouts` côté collecte — fait converger les `pending` bloqués (webhook
      jamais arrivé) via `verify_payment`, même bornage anti-retry-infini
      (`COLLECT_RECONCILIATION_MAX_ATTEMPTS`). Sans cette tâche, la contrainte unique
      bloquerait indéfiniment toute nouvelle tentative de paiement si personne ne revient
      relancer `initiate_payment`.
- [x] Settings `COLLECT_RECONCILIATION_MINUTES`/`_MAX_ATTEMPTS` (`back_sentaa/settings.py`,
      `.env.example`), mêmes valeurs par défaut que les payouts (30 min / 5 tentatives).

Point mineur résiduel, non bloquant (relevé par `escrow-reviewer`) : entre la réservation du
slot et sa mise à jour post-appel fournisseur, un lecteur concurrent (`pending_payment` ou une
2e requête `initiate_payment`) peut lire un payload transitoire avec `payment_flow`/
`checkout_url` encore vides — fenêtre de l'ordre de la latence réseau vers le fournisseur,
aucun impact financier (pas de second appel fournisseur déclenché). À surveiller si le
frontend doit un jour gérer ce cas ; ne justifiait pas de bloquer cette PR.

Fichiers touchés : `escrow/models.py`, `escrow/migrations/0012_paymenttransaction_checkout_url_
and_more.py`, `escrow/services/collect.py` (nouveau), `escrow/views.py`, `escrow/tasks.py`,
`escrow/tests.py`, `back_sentaa/settings.py`, `.env.example`.

## Questions ouvertes restantes
Voir `docs/AUDIT_BACKEND.md` §9. Un agent ne doit jamais trancher ces
questions seul — elles doivent être posées explicitement au développeur.

1. ~~Paiement multi-fournisseurs~~ — tranché en PR 4 (code mort supprimé), **rouvert et
   implémenté en PR 7** (KPay intégrateur principal, NotchPay secondaire, sélection via
   `PAYMENT_PROVIDER`) suite à un problème de fiabilité des webhooks NotchPay en prod.
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
