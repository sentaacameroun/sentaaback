# Audit backend Sentaa — état des lieux (lecture seule)

> Audit réalisé en lecture seule. Aucun fichier du projet n'a été modifié.
> Chaque affirmation renvoie à `chemin/fichier.py:ligne`. Les mentions **[constaté]**
> proviennent de la lecture directe du code ; **[supposé]** signale une inférence non
> vérifiée par exécution ; **[non vérifié]** signale une question laissée ouverte.
> Périmètre lu : configuration, routage, tous les modèles, tous les serializers/vues,
> services d'intégration (paiement/livraison/OTP), tâches Celery, tests, CI, Docker.
> Non exécuté : la base n'a pas été migrée ni lancée ; les intégrations tierces n'ont pas
> été appelées.

---

## 1. Résumé exécutif

Sentaa **n'est pas** un « tas de fichiers bancal ». C'est un projet Django 5 / DRF
correctement découpé en couches (models / serializers / views / services / permissions),
abondamment commenté, avec des choix sains : montants en `DecimalField` (jamais en float),
JWT + OTP, permissions objet dans la plupart des apps, machine à états explicite côté
livraison, transactions SQL + verrous sur le chemin critique de confirmation, validation
d'images réelle (Pillow), Celery pour l'asynchrone, 77 tests. Le socle est réutilisable.

Les défauts sont **localisés**, pas systémiques — mais certains sont graves et concentrés
sur l'argent :
- **Double reversement possible** : `confirm_reception` (acheteur) et `confirm_delivery`
  (coursier) clôturent la commande et libèrent les fonds via deux gardes indépendantes,
  sans idempotence (escrow/views.py:83, logistics/views.py:137).
- **Deux designs de paiement superposés** : le chemin réellement branché appelle NotchPay
  en dur, alors qu'une abstraction multi-fournisseurs de **565 lignes est du code mort**
  (escrow/services/providers/, webhook_handlers.py).
- **IDOR sur les annonces** : n'importe quel utilisateur authentifié peut modifier/supprimer
  l'annonce d'un autre (marketplace/views.py:57, aucune permission objet).
- **DELETE d'une commande** exposé, qui cascade et **détruit le journal des transactions**
  (escrow/views.py:24, escrow/models.py:104).
- **Frais de livraison contrôlés par l'acheteur** (escrow/serializers.py:60).

**Verdict : refactor progressif, pas réécriture.** L'architecture est la bonne ; réécrire
reviendrait à jeter un code structuré, testé et documenté pour re-résoudre les mêmes
problèmes. La seule zone à repenser (pas réécrire tout) est la **machine à états de la
commande escrow** : la centraliser dans un service unique, idempotent, avec un seul chemin
de clôture. Estimation : ~70 % réutilisable tel quel, ~22 % à refactorer, ~8 % à jeter.

---

## 2. Stack & volumétrie

| Élément | Valeur | Preuve |
|---|---|---|
| Langage / runtime | Python 3.12 | `python3 --version`, requirements |
| Framework | Django 5.0.14 + Django REST Framework 3.16 | requirements.txt |
| API async / WebSockets | Channels + channels-redis (ASGI) | settings.py:108, back_sentaa/asgi.py:32 |
| ORM / DB | Django ORM / PostgreSQL (psycopg2) | settings.py:200-210 |
| Auth | SimpleJWT + OTP maison + django-axes (admin) | settings.py:167, users/views.py |
| Cache / broker | Redis (django-redis + Celery broker DB /2) | settings.py:113-125 |
| Tâches async | Celery + beat + flower | back_sentaa/celery.py, docker-compose.prod.yml:65-91 |
| Stockage fichiers | Cloudinary (images) + S3/local (CV) | settings.py:349-376 |
| Paiement | NotchPay (Mobile Money XAF) — branché ; KPay/MoneyFusion présents mais **non branchés** | escrow/services/ |
| SMS / email | Twilio / SMTP, avec fallback « console » en dev | settings.py:398-415 |
| Conteneurisation | Dockerfile + 3 docker-compose + entrypoint | racine |
| CI/CD | GitHub Actions → build image GHCR → SSH deploy VPS | .github/workflows/deploy.yml |
| Doc API | drf-spectacular (Swagger, **DEBUG only**) | back_sentaa/urls.py:19-27 |
| Qualité | pre-commit (black, ruff, reorder-imports) — **local only** | .pre-commit-config.yaml |

**Volumétrie** (`.py`, hors `__pycache__`, hors migrations) : **8 350 lignes** au total.

| App | LOC (hors migr.) | Migrations | Rôle |
|---|---|---|---|
| escrow | 1 361 | 8 | Commandes, paiement, séquestre |
| users | 1 011 | 7 | Comptes, OTP, coursier |
| marketplace | 979 | 4 | Annonces, catégories, offres, favoris |
| logistics | 929 | 4 | Livraison, coursier, suivi temps réel |
| chat | 490 | 1 | Conversations, messages, WS |
| jobs | 447 | 3 | Offres d'emploi, candidatures, CV |
| notifications | 336 | 0 (pas de modèle) | Tâches Celery + emails |
| companies | 332 | 2 | Profils entreprise |
| common | 293 | — | Validation/champs images partagés |
| back_sentaa | 522 | — | Config projet |

Note : ~**565 LOC de code mort** dans escrow/services (providers/ + webhook_handlers +
kpay_client + moneyfusion_client), comptées ci-dessus mais jamais importées hors de
leur propre package (§ 6.5, § 12).

---

## 3. Cartographie de l'architecture actuelle

Découpage **par domaine métier** (une app Django = un domaine), avec une couche `services`
là où il y a de l'intégration tierce ou de la logique réutilisable. Le pattern DRF standard
(ViewSet → Serializer → Model) est respecté partout ; la logique métier vit majoritairement
dans les **serializers** (`.create()`, `.validate()`) et les **actions de vues**, plus une
couche `services/` pour escrow et logistics. Peu de logique dans les modèles (bon signe :
pas de « fat models » fourre-tout).

```
                         HTTP (DRF)            WebSocket (Channels/ASGI)
                             |                          |
              back_sentaa/urls.py            back_sentaa/asgi.py
                             |                  JWTAuthMiddleware (chat/middleware.py)
        +--------+-----------+-----------+            |
        |        |           |           |     chat.consumers / logistics.consumers
     users   marketplace   jobs     companies         |
        |        |           |           |     groups Redis (chat, suivi coursier)
        |        |           |           |
        +----+---+-----+-----+----+------+
             |         |          |
          escrow   logistics    chat        <-- domaines "transactionnels"
             |         |
   +---------+----+    +--- services.py (create_delivery_for_order, dispatch coursiers)
   | services/    |    +--- geo.py (haversine)
   |  payouts.py  |
   |  delivery_hooks.py  --(import différé)--> logistics.services
   |  notchpay_client.py  ----> NotchPay API (Mobile Money)
   |  [providers/ , webhook_handlers.py, kpay_*, moneyfusion_*]  <== CODE MORT (non branché)
   +--------------+
             |
    notifications/tasks.py (Celery beat: rappels paiement/réception/candidature, newsletter)
             |
    common/images/ (validators Pillow, CloudinaryImageField, URLs signées)  <- partagé
```

**Sens des dépendances** : `escrow` → `marketplace` (Listing) ; `logistics` → `escrow`
(Order, services) ; `escrow` → `logistics` **uniquement via import différé** pour éviter
le cycle (escrow/services/delivery_hooks.py:11-16, commenté). `chat`/`companies` référencés
par chaînes `"app.Model"`. Le découpage des dépendances est **maîtrisé et volontaire**
(commentaires à l'appui, escrow/services/payouts.py:64-72).

**Contournements de couche constatés** :
- Les appels réseau tiers (NotchPay, OTP SMS/email, Cloudinary) sont faits **directement
  dans les vues/serializers** de façon synchrone (§ 6.8), pas derrière une file.
- L'upload d'images ne passe pas par le serializer mais par `_attach_uploaded_images`
  dans la vue (marketplace/views.py:92) — assumé et commenté, mais c'est de la logique
  d'I/O dans le contrôleur.

---

## 4. Modèle de données reconstitué

Clés primaires : **UUID** sur presque tous les modèles métier (bon pour une API publique) ;
sauf `ListingImage`, `ListingFavorite`, `JobOfferFavorite`, `Delivery`, `TalentProfile`,
`Conversation.participants` (through) qui gardent un **PK auto-incrémenté** → incohérence
de convention mineure (marketplace/models.py:97, logistics/models.py:9).

### users.User (AbstractBaseUser) — users/models.py:33
- `id UUID PK` ; `phone_number` (unique, indexé, **USERNAME_FIELD**) ; `first_name`,
  `last_name` ; `email` (unique, **null/blank** — plusieurs comptes sans email possibles) ;
  `newsletter_opt_in bool` ; `is_active/is_staff` ; `date_joined`.
- Rôles par **booléens** : `is_seller`, `is_recruiter`, `is_courier` (pas de table de rôles).
- Géo : `latitude/longitude DECIMAL(9,6)`, `location_updated_at`, `is_available`.
- Coursier : `courier_application_status` (choices none/pending/approved/rejected),
  `courier_vehicle_type`, `courier_id_document` (CloudinaryField `type=authenticated`).
- Remarque : `set_password` existe mais **le login API n'utilise jamais le mot de passe**
  (OTP only) — le password ne sert qu'à l'admin Django (§ 6.4).

### marketplace — marketplace/models.py
- **Category** : `id UUID`, `name`, `slug (unique)`, `icon` (Cloudinary).
- **Listing** : `id UUID`, `seller FK→User CASCADE`, `category FK→Category PROTECT`,
  `company FK→CompanyProfile SET_NULL`, `title (indexé)`, `description`,
  `price DECIMAL(12,2) ≥0`, `city (défaut "Douala")`, `status` (active/sold/archived),
  `is_promoted`, timestamps. Index composite `(status, city)`.
- **ListingImage** : PK auto, `listing FK CASCADE`, `image` (Cloudinary), `is_main`.
- **Offer** (négociation) : `id UUID`, `listing FK CASCADE`, `buyer FK CASCADE`,
  `proposed_price DECIMAL(12,2)`, `status` (pending/countered/accepted/rejected),
  `last_offered_by FK→User`, `message`. **Contrainte `unique_together(listing, buyer)`**.
- **ListingFavorite** : `unique_together(user, listing)`.

### escrow — escrow/models.py
- **Order** : `id UUID`, `buyer FK→User PROTECT`, `listing FK PROTECT`,
  `offer FK→Offer SET_NULL`, `item_price`, `shipping_fee (défaut **1**)`, `service_fee`,
  `total_amount` (tous `DECIMAL(12,2)`), `status` (indexé : pending / paid_escrow /
  shipped / received / completed / disputed / refunded), destination GPS
  `DECIMAL(9,6)` + `destination_label`, `paid_at`, `payout_at`,
  `payment_reminder_sent_at`, `reception_reminder_sent_at`, timestamps.
  - ⚠️ Le statut **`received`** est déclaré (models.py:18) mais **jamais utilisé** par le
    code — la clôture passe directement `shipped → completed` (§ 6.5).
- **PaymentTransaction** : `id UUID`, `order FK CASCADE`, `transaction_type`
  (collect/withdraw/courier_payout), `external_ref (unique, nullable)`,
  `provider` (notchpay/kpay/moneyfusion — **blank, jamais renseigné par le code branché**),
  `provider_reference`, `amount DECIMAL(12,2)`, `channel` (mtn/orange), `phone_number`,
  `status` (indexé), `is_success bool` (**redondant** avec `status`), `raw_response JSON`.
  - ⚠️ `order on_delete=CASCADE` : supprimer une commande **efface son journal financier**
    (§ 6.5, § 7 BLOQUANT).

### logistics — logistics/models.py (table `deliveries`)
- **Delivery** : PK auto, `order OneToOne→Order CASCADE`, `courier FK→User SET_NULL`,
  `tracking_number (unique, auto)`, `status` (indexé : pending_assignment / assigned /
  picked_up / in_transit / delivered / failed), `confirmation_code (6 chiffres, `secrets`)`,
  `confirmation_attempts`, `confirmation_locked_until`, coordonnées pickup/destination/
  courier `DECIMAL(9,6)`, `courier_location_updated_at`, jalons temporels, `notes`.
  Génération auto du tracking + code dans `save()` (models.py:72).

### jobs — jobs/models.py
- **TalentProfile** : OneToOne→User, `bio`, `skills (CSV texte)`, `portfolio_url`,
  `experience_years`. ⚠️ **Aucun endpoint ne l'expose** (pas dans jobs/urls.py) → modèle
  quasi orphelin (§ 12).
- **JobOffer** : `id UUID`, `recruiter FK CASCADE`, `company FK→CompanyProfile SET_NULL`,
  `title (indexé)`, **`company_name` (texte libre, redondant avec `company.name`)**,
  `description`, `location`, `is_remote`, `salary_range (texte libre)`, `is_active`.
- **JobApplication** : `id UUID`, `job FK CASCADE`, `applicant FK CASCADE`,
  `cv_file FileField` (`upload_to="cvs/%Y/%m/"`, **validateur extension `.pdf` only,
  aucune limite de taille, aucun contrôle MIME**), `message`, `status`, `applied_at`,
  `reminder_sent_at`. `unique_together(job, applicant)`.
- **JobOfferFavorite** : `unique_together(user, job)`.

### companies.CompanyProfile — companies/models.py:15
- `id UUID`, `owner OneToOne→User CASCADE`, `name`, `description`, `logo` (Cloudinary),
  `sector`, `website`, `rccm_number`, `is_verified` (posé par l'admin), `created_at`.

### chat — chat/models.py
- **Conversation** : `id UUID`, `participants M2M→User`, `listing FK SET_NULL`,
  `order FK SET_NULL`, `created_at`. Helper `get_or_create_for_listing`.
- **Message** : `id UUID`, `conversation FK CASCADE`, `sender FK CASCADE`, `body`,
  `is_read`, `read_at`, `created_at`.

**Incohérences de modèle relevées** : montants **corrects en Decimal** (aucun float —
bon point critique) ; `is_success` redondant avec `status` (escrow/models.py:127) ;
`company_name` dénormalisé sur `Listing`/`JobOffer` alors qu'une FK `company` existe
(marketplace/models.py:66, jobs/models.py:40) ; `skills` en CSV texte au lieu d'une
relation ; PK mixtes UUID/auto ; pas de champ `disputed`/motif de litige exploitable
malgré le statut `disputed`.

---

## 5. Surface API

Aucun **versionnement** (`/api/…` sans `/v1/`). Réponses **non standardisées** : mélange de
`{"error": …}`, `{"detail": …}`, et données serializer brutes selon les vues.
Auth par défaut = `IsAuthenticated` (settings.py:171). Pagination globale 20 (PageNumberPagination).

| Méthode | Chemin | Auth / Rôle | Entrée | Sortie | Notes |
|---|---|---|---|---|---|
| POST | /api/register/ | AllowAny (throttle otp) | phone, noms, email?, password | user + OTP | ⚠️ force OTP **email** même sans email (§ 6.4) |
| POST | /api/otp-request/ | AllowAny (throttle otp) | phone \| email | 200 | énumération de comptes (404 si absent) |
| POST | /api/otp-verify/ | AllowAny (throttle otp) | phone\|email + otp | access+refresh+user | |
| GET/PATCH | /api/me/ | Auth | profil | user | `is_available` modifiable même non-coursier |
| POST | /api/me/apply-courier/ | Auth | vehicle_type, id_document | 201 | valide taille/format image |
| GET | /api/categories/ | **AllowAny** | — | catégories | |
| GET | /api/listings/ | **Auth** | filtres | annonces | ⚠️ incohérent : catégories publiques, annonces non |
| POST | /api/listings/ | Auth | annonce + images | annonce | passe l'user en `is_seller` |
| GET/PUT/PATCH/DELETE | /api/listings/{id}/ | Auth | | | ⚠️ **aucune permission objet** (IDOR, § 6.4) |
| POST | /api/listings/{id}/toggle_favorite/ | Auth | — | favori | |
| GET | /api/listings/favorites/ | Auth | — | annonces | |
| POST/GET | /api/offers/ , /{id}/ | Auth (buyer\|seller) | prix, message | offre | scope correct |
| POST | /api/offers/{id}/accept\|reject\|counter/ | Auth (partie concernée) | prix? | offre | garde « pas 2× la même partie » |
| GET | /api/job-offers/ | AllowAny | filtres | offres | |
| POST | /api/job-offers/ | **IsVerifiedRecruiter** | offre | offre | exige `company.is_verified` |
| PUT/PATCH/DELETE | /api/job-offers/{id}/ | **IsOwnerRecruiter** | | | permission objet OK |
| GET/POST | /api/job-applications/ | Auth (scoped) | cv, message | candidature | recruteur voit celles de ses offres |
| POST | /api/job-applications/{id}/accept\|reject\|mark_reviewed/ | **IsApplicationRecruiter** | — | candidature | permission objet OK |
| GET/PUT/PATCH/DELETE | /api/orders/{id}/ | Auth (buyer\|seller) | | | ⚠️ **DELETE cascade les transactions** (§ 6.5) |
| POST | /api/orders/ | Auth | listing, destination, **shipping_fee** | order | ⚠️ frais livraison client (§ 6.5) |
| POST | /api/orders/{id}/initiate_payment/ | Auth (buyer) | phone, channel | 202 | NotchPay en dur |
| POST | /api/orders/{id}/confirm_reception/ | Auth (buyer) | — | fonds libérés | ⚠️ non atomique, double-payout (§ 6.5) |
| POST | /api/mobile-money-webhook/ | **AllowAny, sans signature** | reference | 200 | re-vérifie via API (atténue) |
| GET | /api/deliveries/ , /{id}/ | Auth (scoped courier\|buyer\|seller\|staff) | | | code confirmation masqué sauf acheteur |
| POST | /api/deliveries/{id}/assign/ | **IsAdminUser** | courier | | |
| POST | /api/deliveries/{id}/claim/ | Auth + **IsCourier** | — | | atomique (premier arrivé) |
| POST | /api/deliveries/{id}/update-status/ | **IsAssignedCourier** | status | | machine à états |
| POST | /api/deliveries/{id}/update-location/ | **IsAssignedCourier** | lat, lng | | push WS temps réel |
| POST | /api/deliveries/{id}/confirm-delivery/ | **IsAssignedCourier** (throttle) | code | livré + payouts | atomique + verrou (§ 6.5) |
| GET/POST/{id} | /api/chat/conversations/ | Auth ; retrieve/messages **IsConversationParticipant** | | | |
| GET/PATCH | /api/companies/me/ | Auth | profil | company | crée + passe `is_recruiter` |
| GET | /api/companies/{uuid}/ | AllowAny | — | profil public + annonces/offres | pas de PII owner |
| GET | /api/schema/ , /api/docs/ | **DEBUG only** | — | OpenAPI/Swagger | absent en prod |

WebSocket (Channels, JWT en query-string) : chat + suivi coursier (asgi.py:32,
chat/routing.py, logistics/routing.py) — non détaillés ici.

---

## 6. Analyse par axe

### 6.1 Architecture générale — **[constaté]**
Séparation de couches **présente et globalement respectée** : models / serializers / views /
`services` / `permissions`. La logique métier vit dans les serializers (`OrderSerializer.create`
calcule commission et total, escrow/serializers.py:49-69) et les actions de vues, avec une
couche `services/` pour les intégrations (escrow/services/, logistics/services.py). Monolithe
modulaire par domaine, **pas** un tas de fichiers. Deux réserves : (a) I/O tierce synchrone
dans les vues (§ 6.8) ; (b) une abstraction de service à moitié construite et non branchée
(§ 6.5). La qualité de commentaire est élevée et explique les décisions (ex. settings.py:81-84,
escrow/services/payouts.py:64-72) — c'est du code entretenu, pas abandonné.

### 6.2 Modèle de données — **[constaté]**
Voir § 4. Points forts : Decimal partout pour l'argent, UUID sur les entités exposées, index
sur les colonnes de filtrage (`status`, `city`, `title`), contraintes `unique_together`
cohérentes. Points faibles : dénormalisation non nécessaire (`company_name`), champ redondant
(`is_success`), `skills` CSV, PK mixtes, statut `received` mort, pas de modèle de litige réel.

### 6.3 Surface API — **[constaté]**
Voir § 5. REST cohérent dans l'ensemble (ViewSets + routers), mais : pas de versionnement,
enveloppe de réponse non standardisée, incohérence public/privé (catégories AllowAny vs
listings Auth ; job-offers AllowAny vs listings Auth), Swagger indisponible en prod
(back_sentaa/urls.py:19). Aucune duplication d'endpoint constatée.

### 6.4 Authentification & autorisation — **[constaté]**
- **Mécanisme** : JWT (SimpleJWT) émis après vérification OTP (users/views.py:127). OTP à 6
  chiffres stocké en cache Redis 5 min, usage unique (users/services/otpservice.py:28-59).
- **Mots de passe** : `set_password` (hash Django standard) mais **inutilisés côté API** — le
  login est OTP only ; le password ne sert qu'à l'admin. Un compte créé sans password ne peut
  de toute façon se connecter que par OTP.
- **Rôles** : booléens `is_seller/is_recruiter/is_courier` + `is_staff`, positionnés par des
  actions (création d'annonce → is_seller ; profil entreprise → is_recruiter ; validation admin
  → is_courier). Pas de table de rôles/permissions granulaires.
- **Refresh / révocation** : ⚠️ **aucun endpoint de refresh ni de logout/blacklist branché**
  (users/urls.py) ; `SIMPLE_JWT` n'est pas configuré → durées par défaut (access 5 min). Un
  refresh token est émis mais **inutilisable** faute de route. Pas de révocation possible.
- **django-axes** protège **uniquement** le formulaire admin (settings.py:157-165) ; l'OTP API
  est protégé par le throttle scope `otp` 5/min (settings.py:182).

**Trous d'autorisation — explicitement :**
1. **IDOR annonces (BLOQUANT)** : `ListingViewSet` (marketplace/views.py:57) n'a **aucune
   permission objet** ; `get_queryset` renvoie toutes les annonces actives ; update/destroy ne
   comparent jamais `listing.seller` à l'utilisateur. → **tout utilisateur authentifié peut
   PATCH/PUT/DELETE l'annonce d'un autre.** (À noter : le même trou existait dans `jobs` et a
   été corrigé par `IsOwnerRecruiter`, jobs/permissions.py:39 — mais pas répliqué ici.)
2. **DELETE commande + cascade (BLOQUANT)** : `OrderViewSet` est un `ModelViewSet` complet
   (escrow/views.py:24) sans `perform_destroy` ni restriction de verbe ; buyer/seller peuvent
   `DELETE /api/orders/{id}/`, ce qui **cascade sur `PaymentTransaction`** (escrow/models.py:104)
   et **détruit le journal financier**, plus la `Delivery` liée.
3. **`is_available` en écriture libre (MINEUR)** : `MeView.patch` permet à n'importe qui de se
   déclarer disponible sans être coursier (users/serializers.py:42 hors read_only).
4. **Énumération de comptes (MINEUR)** : otp-request renvoie 404 si l'utilisateur n'existe pas
   (users/views.py:101).

Bonnes protections objet **présentes** ailleurs : offers (scope buyer/seller,
marketplace/views.py:203-229), job-applications (jobs/permissions.py:28-51), deliveries
(logistics/permissions.py), chat (IsConversationParticipant), companies (OneToOne owner).

### 6.5 Flux escrow / paiement — **[constaté]** — zone la plus problématique
**Machine à états de la commande** : les statuts sont **explicites** (champ `status`,
escrow/models.py:14) mais les **transitions sont dispersées** dans plusieurs vues de deux
apps, sans service centralisé :
```
 pending ──initiate_payment(NotchPay)──▶ [txn collect pending]
        ──webhook success──▶ paid_escrow ──on_order_paid──▶ crée Delivery
 paid_escrow ──courier picked_up (logistics.update_status)──▶ shipped
 shipped ──confirm_reception (acheteur)──────────▶ completed + release_escrow_funds
 in_transit ──confirm_delivery (coursier, code)──▶ completed + release_escrow_funds + pay_courier
 (disputed / refunded / received : déclarés, jamais atteints par le code)
```
- **Double reversement (BLOQUANT)** : deux chemins de clôture indépendants libèrent les fonds.
  `confirm_reception` exige `order.status == "shipped"` (escrow/views.py:89) ;
  `confirm_delivery` exige `delivery.status == "in_transit"` (logistics/views.py:141). Or pendant
  le transit, `order.status` **vaut encore "shipped"** (mis à "shipped" au pickup,
  logistics/views.py:131). Les deux gardes portent sur des objets différents et s'ignorent.
  `release_escrow_funds` (escrow/services/payouts.py:10) **n'a aucune idempotence** (ne regarde
  ni `payout_at` ni une transaction `withdraw` existante). → **le vendeur peut être payé deux
  fois.** Aucun test ne couvre ce scénario (escrow/tests.py teste chaque chemin isolément).
- **Idempotence webhook (OK)** : `MobileMoneyWebhookView` utilise `select_for_update` +
  garde `status != "pending"` (escrow/views.py:116-127), testée (escrow/tests.py:110).
- **Transactions SQL sur l'argent** : ✅ dans `confirm_delivery` (atomic + select_for_update,
  logistics/views.py:154) et le webhook ; ❌ **`confirm_reception` n'est ni atomique ni
  verrouillé** (escrow/views.py:83-101) : `order.status = "completed"` est sauvé **avant**
  `release_escrow_funds`, et deux requêtes concurrentes passent toutes deux la garde.
- **Réponse fournisseur en double / retard / jamais** : double → idempotence webhook OK ;
  jamais → tâche Celery `check_pending_escrow_payments` relance un rappel (notifications/tasks.py:23)
  mais **ne réconcilie pas** le paiement ; les reversements (`withdraw`/`courier_payout`) sont
  créés `pending` et **jamais réconciliés** (le webhook ne vérifie que via `/payments/{ref}`,
  pas `/transfers/`, escrow/services/notchpay_client.py:44) → un transfert reste `pending`
  indéfiniment.
- **Journal d'audit immuable** : ❌ `PaymentTransaction` est **supprimable** par cascade via
  l'API (point 2 ci-dessus) et modifiable en base ; l'admin le met en lecture seule
  (escrow/admin.py:49) mais n'empêche pas la suppression.
- **Litiges / remboursements / annulations** : ❌ **non implémentés** — statuts `disputed`/
  `refunded` déclarés mais aucun endpoint ni logique ne les produit.
- **Frais de livraison contrôlés par le client (MAJEUR)** : `shipping_fee` n'est **pas** en
  `read_only` (escrow/serializers.py OrderSerializer) ; l'acheteur le fixe dans la requête de
  création (`validated_data.get("shipping_fee", 0)`, serializers.py:60) et il détermine le
  paiement du coursier (`pay_courier_for_delivery` paie `order.shipping_fee`,
  payouts.py:75). Le calcul distance (`logistics/geo.haversine_km`) existe mais n'est **pas**
  utilisé pour le tarif.
- **Deux designs superposés (MAJEUR)** : le chemin branché appelle `NotchPayClient` **en dur**
  (escrow/views.py:52, payouts.py:26). En parallèle, une abstraction multi-fournisseurs
  (escrow/services/providers/router.py + base.py + kpay.py + moneyfusion.py + notchpay.py),
  `webhook_handlers.apply_collection_result`, et les clients `kpay_client`/`moneyfusion_client`
  (**565 LOC**) **ne sont importés par rien hors de leur propre package** (vérifié par grep).
  Les commentaires du modèle affirment pourtant une « chaîne de fallback avec résilience
  automatique » (escrow/models.py:80) : **le code ne fait pas ce que le commentaire décrit.**

### 6.6 Intégration livraison — **[constaté]**
- **Découplage** : la création de livraison passe par un hook (`on_order_paid` →
  `create_delivery_for_order`, delivery_hooks.py + logistics/services.py:12) — bonne
  abstraction. Le paiement coursier vit dans `escrow.services.payouts` (justifié pour éviter
  le cycle d'import, payouts.py:64).
- **Machine à états** : explicite et **validée** (`TRANSITIONS`, logistics/views.py:35-39 ;
  transition contrôlée logistics/views.py:111). `claim` est **atomique** (UPDATE conditionnel,
  logistics/views.py:94). `confirm_delivery` : anti-brute-force (5 essais, lockout 15 min,
  `secrets.compare_digest`), throttle dédié, atomic + verrou (logistics/views.py:137-191).
  **C'est l'app la mieux écrite du projet.**
- **Erreurs / retries / timeouts tiers** : `NotchPayClient` a un timeout (15 s,
  notchpay_client.py:20) mais **aucun retry**. Les payouts sont **fail-soft** (log + txn
  `failed`, réconciliation manuelle, payouts.py:48) — acceptable mais pas de reprise auto.
- **Transporteur indisponible** : il n'y a **pas de transporteur tiers** — la livraison est
  gérée en interne par des coursiers de la plateforme (modèle Uber-like). Donc pas de
  dépendance API transporteur ; le risque tiers est côté paiement uniquement.

### 6.7 Module Jobs — **[constaté]**
- Offre/candidature corrects, `unique_together(job, applicant)`, permissions objet OK
  (jobs/permissions.py). Cycle : pending → reviewed/accepted/rejected via actions recruteur.
- **Upload CV (MAJEUR)** : `cv_file` = `FileField` avec **seulement** `FileExtensionValidator(["pdf"])`
  (jobs/models.py:69) → validation **sur l'extension du nom uniquement**, **aucun contrôle de
  type MIME, aucune limite de taille**. Un fichier arbitraire renommé `.pdf`, ou un PDF de
  plusieurs Go, passe (abus de stockage / contenu non maîtrisé).
- **Accès au fichier** : le CV est sur le **stockage par défaut** (S3 si configuré, sinon disque
  local, settings.py:353). L'URL est renvoyée par `JobApplicationSerializer.cv_file`
  (jobs/serializers.py:60) **uniquement** aux utilisateurs autorisés (queryset scoped,
  jobs/views.py:73). **Mais** :
  - En **local/dev** l'URL est `/media/cvs/AAAA/MM/<nom>.pdf`, servie via `static()`
    **seulement si DEBUG** (users/urls.py:17) → devinable et non protégée par auth si DEBUG=True.
  - En **prod avec S3** : URL signée expirante (django-storages, `AWS_QUERYSTRING_AUTH` défaut)
    → non devinable **[supposé, non vérifié]** (dépend de la config S3 réelle).
  - En **prod sans S3** : disque éphémère, fichier perdu au redéploiement (settings.py:335) et
    non servi → CV inaccessible.
- **TalentProfile** : modèle présent mais **aucun endpoint** → mort (§ 12).

### 6.8 Traitements asynchrones — **[constaté]**
- **File présente** : Celery + Redis + beat + flower, réellement lancés en prod
  (docker-compose.prod.yml:65-91). Tâches planifiées : rappels paiement/réception/candidature
  + newsletter (settings.py:131-148, notifications/tasks.py) — bien faites (dédup via
  `*_reminder_sent_at`, gestion d'exception par item).
- **Fait en synchrone dans le cycle requête/réponse (à tort)** :
  - **OTP SMS/email** (Twilio/SMTP) à l'inscription et au login (users/views.py:54,94) → un
    appel réseau bloquant sur des endpoints publics.
  - **Initiation de paiement NotchPay** (escrow/views.py:54) et **reversements**
    (payouts.py:28,90) → HTTP tiers bloquant dans la requête / la transaction utilisateur.
  - **Uploads Cloudinary** (marketplace/views.py:133, users/views.py) → bloquant.
  Aucune de ces opérations n'est déportée sur Celery. Sous latence tierce, les requêtes
  pendent (timeout 15 s pour NotchPay).

### 6.9 Fiabilité & observabilité — **[constaté]**
- **Erreurs** : pas de handler DRF centralisé custom ; le webhook a un `try/except` global
  (escrow/views.py:159). Fail-soft explicite sur les payouts. Correct sans être industrialisé.
- **Logs** : `logging` structuré vers la console (settings.py:418) ; `logger.exception` utilisé.
  ⚠️ L'OTP est **loggé en clair** en backend console (otpservice.py:48) — acceptable en dev,
  à bannir en prod (dépend de `SMS_BACKEND`).
- **Health check** : ❌ aucun endpoint applicatif `/health` (grep négatif) ; seul Celery a un
  healthcheck conteneur (docker-compose.prod.yml:72).
- **Monitoring / APM** : ❌ pas de Sentry ni équivalent (grep négatif).

### 6.10 Sécurité — **[constaté]**
- **Secrets** : ✅ `.env` **non commité** (git ls-files négatif ; `.gitignore:108`). Toutes les
  clés sont lues via `os.getenv` (settings.py). `.env.example` documente sans valeurs réelles.
- **Validation d'entrée** : serializers DRF partout ; validation d'image réelle par Pillow
  (common/images/validators.py) avec limites taille + pixels + format. **Sauf** CV (§ 6.7).
- **Injection SQL** : ORM only, pas de `raw()`/f-string SQL constaté → risque faible.
- **CORS** : allow-list via env (settings.py:86), pas de wildcard par défaut.
- **Rate limiting** : throttles globaux (100/j anon, 1000/j user) + scopes `otp` et
  `delivery_confirmation` (settings.py:179-184). Correct.
- **Webhook non signé (MAJEUR)** : `MobileMoneyWebhookView` est `AllowAny` **sans vérification
  de signature HMAC** du fournisseur (escrow/views.py:106). Atténué car il **re-vérifie** le
  statut via l'API NotchPay (impossible de forger un succès), mais expose un endpoint qui
  déclenche des appels sortants sur simple `reference` fournie (amplification / bruit).
- **HTTPS / cookies / HSTS** : durcis conditionnellement en prod (settings.py:379-391). Bon.
- **Exposition de PII** : `JobApplicationSerializer` expose nom + **téléphone** du candidat au
  recruteur (jobs/serializers.py:70) — attendu fonctionnellement, mais c'est de la PII. Profil
  entreprise public n'expose pas l'owner (companies/serializers.py:29). Pas de fuite large
  constatée.

### 6.11 Tests & qualité — **[constaté]**
- **77 fonctions de test** (pytest-django) réparties : users 21, logistics 12, marketplace 11,
  companies 7, chat 6, escrow 6, notifications 4, jobs **3 seulement**, common 7. Les tests
  escrow couvrent commission, idempotence webhook, payout — **mais pas** le double-payout ni
  l'IDOR annonces ni le DELETE commande.
- **Lint/format** : ruff + black + reorder-imports en **pre-commit local** (.pre-commit-config.yaml)
  — versions un peu anciennes (black 23.1 en hook vs 26.1 en requirement : incohérence).
- **Typage** : aucun (`mypy` absent des dépendances ; pas d'annotations systématiques).
- **CI (MAJEUR)** : ⚠️ `.github/workflows/deploy.yml` **build + déploie sur chaque push** vers
  `dev`/`main` **sans exécuter les tests ni le lint**. Les 77 tests ne tournent **jamais** en CI.

### 6.12 Dette & incohérences — **[constaté]**
- **Code mort** : ~565 LOC provider/paiement non branchées (§ 6.5) ; `TalentProfile` sans
  endpoint ; statuts `received`/`disputed`/`refunded` sans logique ; `is_success` redondant.
- **Duplications** : logique de complétion+payout dupliquée entre `confirm_reception` et
  `confirm_delivery` ; `company_name` dupliqué avec la FK company.
- **TODO/FIXME** : **0** (grep) — le code est propre de ce côté.
- **Dépendances** : 6 non épinglées (`celery`, `channels`, `channels-redis`, `flower`,
  `requests`, `twilio`) → builds non reproductibles ; Django « généré par 6.0.2 » en
  commentaire (settings.py:4) mais **5.0.14** installé.
- **Configs contradictoires** : `render.yaml` (déploiement Render, legacy) coexiste avec
  `.github/workflows/deploy.yml` (VPS) + **3** `docker-compose*.yml` ; `ALLOWED_HOSTS` par
  défaut pointe encore `sentaaback.onrender.com` (settings.py:34) alors que le déploiement
  cible `*.sentaa.net`. Vestiges de plusieurs cibles de déploiement superposées.

---

## 7. Problèmes classés par gravité

### BLOQUANT (argent / accès non autorisé — à traiter avant toute mise en ligne)
1. **Double reversement au vendeur** — deux chemins de clôture sans idempotence
   (escrow/views.py:83, logistics/views.py:137, escrow/services/payouts.py:10).
2. **IDOR sur les annonces** — modification/suppression de l'annonce d'autrui
   (marketplace/views.py:57 : pas de permission objet).
3. **DELETE commande → destruction du journal financier** par cascade
   (escrow/views.py:24, escrow/models.py:104).
4. **`confirm_reception` non atomique et non verrouillé** — incohérence possible statut/fonds
   (escrow/views.py:83-101).

### MAJEUR
5. **Frais de livraison fixés par l'acheteur**, pilotant le paiement coursier
   (escrow/serializers.py:60, payouts.py:75).
6. **Abstraction paiement multi-fournisseurs non branchée** (565 LOC mortes) contredisant les
   commentaires du modèle (escrow/services/providers/, models.py:80).
7. **Upload CV sans contrôle MIME ni limite de taille** (jobs/models.py:69).
8. **Webhook de paiement sans vérification de signature** (escrow/views.py:106).
9. **CI déploie sans exécuter les tests** (.github/workflows/deploy.yml).
10. **Pas de refresh/révocation JWT branché** (users/urls.py ; SIMPLE_JWT non configuré).
11. **Litiges/remboursements non implémentés** malgré les statuts déclarés.
12. **Reversements jamais réconciliés** (transferts restent `pending`, notchpay_client.py:44).

### MINEUR
13. OTP généré avec `random.randint` (non cryptographique) alors que le code de livraison
    utilise `secrets` — incohérence (otpservice.py:28 vs logistics/models.py:76).
14. Inscription force l'OTP **email** même pour un compte téléphone-seul (users/views.py:54).
15. Énumération de comptes sur otp-request (users/views.py:101).
16. `is_available` modifiable par un non-coursier (users/serializers.py).
17. Incohérence de visibilité publique : catégories/job-offers AllowAny vs listings Auth.
18. Dépendances non épinglées ; `render.yaml` obsolète ; `ALLOWED_HOSTS` legacy ; black 23.1
    vs 26.1 ; `is_success` redondant ; PK mixtes ; `TalentProfile` orphelin.
19. Pas de health check applicatif ni d'APM/Sentry.
20. I/O tierce synchrone dans les vues (OTP, paiement, upload) — latence/robustesse (§ 6.8).

---

## 8. Verdict & recommandation

### Réutilisabilité (estimation chiffrée, justifiée)
- **~70 % réutilisable tel quel** : le socle Django/DRF, le modèle de données (montants en
  Decimal, UUID, index, contraintes), les apps `users`, `marketplace` (hors IDOR), `logistics`
  (la mieux écrite), `chat`, `companies`, la couche `common/images`, les tâches Celery, la
  config, le Docker/CI d'infrastructure.
- **~22 % à refactorer** : la machine à états escrow (centralisation + idempotence + chemin
  unique de clôture), les permissions objet manquantes (annonces, DELETE commande), le
  tarif livraison, l'upload CV, l'async des I/O tierces, le refresh JWT.
- **~8 % à jeter** : l'abstraction provider non branchée (ou la finir — décision produit),
  `render.yaml`, `TalentProfile` mort, champs redondants.

### Structurel vs superficiel
- **Structurellement à repenser (mais borné)** : **uniquement** le cycle de vie de la commande
  escrow et de ses paiements — aujourd'hui éclaté sur deux apps et deux chemins concurrents.
  C'est le seul endroit qui demande un vrai redesign (un service de transitions + un registre
  de mouvements de fonds idempotent).
- **Superficiel (nettoyage local)** : IDOR annonces (ajouter une permission objet, déjà
  existante dans `jobs`), DELETE commande (retirer le verbe), tarif livraison (passer
  `shipping_fee` en calculé/read-only), CV (ajouter validateur taille+MIME), CI (ajouter un job
  test), code mort (supprimer), deps (épingler). Tout cela est local et livrable isolément.

### Arguments des deux camps
**Pour la réécriture** : le flux argent est bugué et éclaté ; il existe deux designs de paiement
superposés (dette de conception) ; certains statuts/modèles sont morts ; on peut repartir de
zéro sans données à migrer.
**Pour le refactor** : l'architecture est déjà la bonne (découpage par domaine, couches
respectées, dépendances maîtrisées) ; le code est **testé (77 tests), documenté, outillé**
(pre-commit, Docker, CI/CD) ; les défauts sont **énumérables (~12) et localisés**, pas une
pourriture diffuse ; réécrire jetterait cet actif pour re-rencontrer les mêmes problèmes
métier (escrow, OTP, dispatch coursier) déjà résolus ici.

### Recommandation : **REFACTOR PROGRESSIF** (avec redesign ciblé du noyau escrow)
Réécrire serait un gaspillage : on remplacerait un code structuré et testé par du code neuf
non testé, pour un gain architectural quasi nul (l'archi cible serait… la même). Le bon
mouvement est un refactor où **seul le cœur transactionnel escrow est repensé**, le reste étant
corrigé localement.

**Ordre suggéré (étapes indépendamment livrables — non détaillé, à cadrer ensemble) :**
1. **Colmater les BLOQUANTs de sécurité** (permission objet annonces ; retirer DELETE commande ;
   rendre `confirm_reception` atomique). Livrable seul, faible risque.
2. **Centraliser la clôture escrow** : un service unique de transition + idempotence de
   `release_escrow_funds` (verrou + garde sur `payout_at`/transaction existante) + un seul
   chemin de complétion. Cœur du chantier.
3. **Trancher la question paiement** : brancher l'abstraction provider **ou** la supprimer
   (ne pas laisser 565 LOC mortes qui mentent sur le comportement réel).
4. **Tarif livraison** côté serveur ; **CV** validé (taille+MIME) ; **I/O tierces** vers Celery ;
   **refresh JWT**.
5. **Durcir la CI** (tests + lint bloquants avant deploy) ; épingler les deps ; supprimer le
   code mort et les configs obsolètes.

### Les 3 facteurs décisifs
1. **Le découpage en couches est déjà correct et homogène** sur 10 apps → les fondations
   structurelles n'ont pas besoin d'être refaites.
2. **Les défauts sont localisés et dénombrables**, pas systémiques → le coût de correction est
   très inférieur à celui d'une réécriture.
3. **Tests + docs + outillage existent déjà** → le refactor est sûr et observable, là où une
   réécriture repartirait sans filet.

### Éléments à conserver absolument (en cas de refactor comme de réécriture)
Le schéma de données (Decimal/UUID/index/contraintes), l'app `logistics` (machine à états,
confirmation atomique, dispatch géo), `common/images` (validation Pillow + URLs signées),
l'intégration NotchPay déjà écrite, le flux OTP, la config Celery/Channels, l'infra Docker/CI.

---

## 9. Questions ouvertes pour le développeur

1. **Paiement multi-fournisseurs** : l'abstraction `providers/` + `webhook_handlers` était-elle
   en cours de branchement (à finir) ou abandonnée (à supprimer) ? Le commentaire du modèle
   décrit un comportement que le code branché n'a pas. **[non vérifié]**
2. **Double clôture escrow** : quel est le chemin *voulu* de fin de commande — l'acheteur
   confirme la réception, **ou** le coursier saisit le code, **ou** les deux ? Qui déclenche le
   paiement du vendeur, et à quel instant précis ?
3. **Frais de livraison** : doivent-ils être calculés par distance (le `haversine` existe) et
   fixés côté serveur, ou l'entrée client actuelle est-elle intentionnelle ?
4. **Litiges/remboursements** : les statuts `disputed`/`refunded` sont-ils un besoin réel à
   implémenter, ou des reliquats à retirer du modèle ?
5. **Stockage des CV en production** : S3 est-il effectivement configuré (bucket privé, URLs
   signées) ? Sinon, comment un recruteur télécharge-t-il un CV, le disque étant éphémère ?
   **[non vérifié — dépend de l'environnement réel]**

---

### Annexe — méthode & limites de l'audit
Lecture exhaustive des sources Python (config, urls, 10 apps : models/serializers/views/
services/permissions), des tests, de la CI et du Docker. **Non fait** : exécution des tests,
migration/lancement de la base, appels réels aux API tierces, revue ligne à ligne des
consumers WebSocket et des fichiers `admin.py`/`emails.py`/`geo.py` (survolés). Les points
`[supposé]`/`[non vérifié]` signalent là où une confirmation par exécution serait nécessaire.
