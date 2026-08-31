# Plan — Notifications push Sentaa (cross-repo : backend + Flutter)

Ce fichier existe en double, à la racine des deux repos (backend et
Flutter), avec le même contenu — les deux équipes/sessions doivent voir le
même état d'avancement. Mets à jour les deux copies ensemble.

Prérequis : PR1-PR6 backend terminées, tâches 1-7 Flutter terminées (le
flux commande/paiement doit être fiable avant d'y accrocher des
notifications — sinon on notifie correctement un flux qui reste cassé).

## Règle d'or (les deux côtés)

**L'envoi n'a jamais lieu côté Flutter.** Le client ne fait qu'obtenir son
token FCM et le transmettre au serveur, et réagir aux notifications
reçues. Toute la logique d'envoi (API HTTP v1, identifiants de compte de
service) vit exclusivement côté backend. Si un agent, quel qu'il soit,
propose d'appeler l'API FCM directement depuis le code Dart avec des
identifiants embarqués dans l'app, c'est un trou de sécurité — refuser.

## Backend

### BE-PUSH-1 — Infrastructure d'envoi
Statut : ☑ terminé (2026-08-29) — `pytest notifications/` = **21 passed** ; `pytest` nu =
**128 passed** ; `ruff check .` clean ; `black --check .` clean ; `manage.py check` OK.
Commande : `/push-infra-setup`
- [x] Modèle `DeviceToken` (app `notifications/`) — `user` (FK CASCADE), `token`
      (unique), `platform` (android/ios), `device_id` optionnel, `active` (défaut
      `True`, désactivé jamais supprimé), `created_at`/`updated_at`.
- [x] Endpoints register/unregister de token — `POST
      /api/notifications/register-device/` (upsert par `token`, réattribue et
      réactive si le token existait déjà sous un autre utilisateur) et `POST
      /api/notifications/unregister-device/` (idempotent, scope strict sur
      `request.user` — ne peut jamais désactiver le token d'un tiers). Les deux
      authentifiés (`IsAuthenticated`).
- [x] Service d'envoi via `firebase-admin` (API v1), asynchrone (Celery) —
      `notifications/services/push.py::send_push` (API v1 `firebase_admin.messaging`
      exclusivement, jamais l'ancienne API legacy) + tâche
      `notifications/tasks.py::send_push_notification_task`, seul point d'entrée
      (`.delay(...)`) : aucun appel synchrone au service ailleurs dans le code de
      cette tâche.
- [x] Nettoyage automatique des tokens invalides/désinscrits — erreurs traitées
      par token individuellement (un token mort ne bloque pas l'envoi aux autres
      appareils) ; `UnregisteredError`/`InvalidArgumentError` désactivent le
      `DeviceToken` correspondant, toute autre erreur FCM est journalisée sans
      désactiver (pas de perte de token sur un incident FCM transitoire).

Note hors scope (pour BE-PUSH-2, ne pas oublier) : `back_sentaa/urls.py` a dû
être touché en plus de la liste du scope initial pour monter
`notifications.urls` (`path("api/", include("notifications.urls"))`) — sans
ça les deux endpoints n'étaient joignables par aucune route. Changement
d'une ligne, pas de logique métier d'une autre app.

⚠️ **Copie Flutter à mettre à jour manuellement** : ce fichier existe en
double dans le repo Flutter (voir note en tête de fichier) — cette session
n'a accès qu'au repo backend, reporter cette case côté Flutter séparément.

### BE-PUSH-2 — Branchement sur les événements métier
Statut : ☑ terminé (2026-08-29) — `pytest escrow logistics marketplace jobs chat
notifications` = **100 passed** ; `pytest` nu = **142 passed** ; `ruff check .`
clean ; `black --check .` clean. Diff `escrow/`+`logistics/` revu par
`escrow-reviewer` (verdict : OK pour clore, aucun blocage).
Commande : `/push-business-hooks`
- [x] Commande payée → notif vendeur (`escrow/services/delivery_hooks.py::on_order_paid`)
- [x] Livraison assignée → notif acheteur (`logistics/views.py::DeliveryViewSet.claim`)
- [x] Statut `delivered` atteint → notif acheteur, « confirme la réception »
      (`escrow/services/order_lifecycle.py::OrderLifecycleService.mark_delivered`)
- [x] Commande `completed` → notif vendeur, « tu as été payé »
      (`OrderLifecycleService.complete_and_release`)
- [x] Offre reçue/acceptée/refusée/contre-proposée → notif partie concernée
      (`marketplace/views.py::OfferViewSet` — `perform_create` + `_respond`)
- [x] Candidature reçue → notif recruteur (`jobs/views.py::JobApplicationViewSet.perform_create`)
- [x] Statut de candidature changé → notif candidat (`JobApplicationViewSet._respond`)
- [x] Nouveau message de chat → notif destinataire(s) hors auteur
      (`chat/consumers.py::ChatConsumer._create_message`)

Tous les appels passent par `transaction.on_commit(lambda: send_push_notification_task
.delay(...))`, jamais dans le chemin critique d'une transaction (voir .claude/rules/
push-notifications.md). Aucun montant ni contenu de message dans le corps des
notifications. `data` porte toujours `{"type": ..., "id": ...}` pour le deep-linking
Flutter (types utilisés : `order`, `offer`, `job_application`, `chat`).

Remarques mineures notées par `escrow-reviewer` (non bloquantes, pour une itération
future) : dans `logistics/views.py::claim`, l'appel `transaction.on_commit()` se
trouve hors d'un bloc `atomic()` explicite (le seul écriture DB est un `.update()`
unitaire déjà validé en autocommit) — sans risque fonctionnel, mais pourrait être
enveloppé dans un `with transaction.atomic():` pour la cohérence de style.

⚠️ **Copie Flutter à mettre à jour manuellement** : cette session n'a accès
qu'au repo backend, reporter cette case côté Flutter séparément (voir note
en tête de fichier).

### BE-PUSH-3 — Rappels existants étendus au push
Statut : ☑ terminé (2026-08-30) — `pytest notifications/` = **25 passed** ; `pytest` nu =
**146 passed** ; `ruff check .` clean ; `black --check .` clean.
Commande : `/push-reminders-integration`
- [x] Rappel de paiement (déjà existant en email/SMS) étendu au push
      (`check_pending_escrow_payments`, notif acheteur, `data.type = "order"`)
- [x] Rappel de réception (déjà existant, déjà corrigé pour cibler
      `delivered` en PR4) étendu au push
      (`check_pending_reception_confirmations`, notif acheteur, `data.type = "order"`)
- [x] Rappel de candidature (déjà existant) étendu au push
      (`check_pending_job_applications`, notif recruteur, `data.type = "job_offer"` —
      rappel agrégé au niveau de l'offre, pas d'une candidature précise, d'où un type
      distinct de `job_application` utilisé pour les événements BE-PUSH-2)

Tous les appels passent par `transaction.on_commit(lambda: send_push_notification_task
.delay(...))`, canal email/SMS existant conservé tel quel dans les trois tâches (le push
s'ajoute après l'écriture du champ `*_reminder_sent_at`, jamais à la place). Le cas
« aucun `DeviceToken` actif » n'est pas re-gardé ici : `notifications/services/push.py
::send_push` (BE-PUSH-1) le traite déjà comme un no-op (0 envoi, aucune exception) —
vérifié par test dédié plutôt que dupliqué.

⚠️ **Copie Flutter à mettre à jour manuellement** : cette session n'a accès
qu'au repo backend, reporter cette case côté Flutter séparément (voir note
en tête de fichier).

## Flutter

### FE-PUSH-1 — SDK, permissions, cycle de vie du token
Statut : ☐ non commencé (dépend de BE-PUSH-1 livré et joignable)
Commande : `/fe-push-sdk-setup`
- [ ] `firebase_messaging` + `flutter_local_notifications` intégrés
- [ ] Permission demandée (iOS explicite, Android 13+ runtime)
- [ ] Token enregistré au login, ré-enregistré sur refresh
- [ ] Token désenregistré au logout (best-effort, ne bloque pas la
      déconnexion — même philosophie que la tâche 5 du plan de sync)

### FE-PUSH-2 — Réception et deep-linking
Statut : ☐ non commencé (dépend de FE-PUSH-1)
Commande : `/fe-push-handling-deeplink`
- [ ] Notification reçue en foreground → affichage local
- [ ] Notification tapée (background/terminated) → navigation go_router vers
      le bon écran (commande, conversation, candidature) selon le payload
- [ ] Aucune donnée sensible (montant exact, contenu de message) dans le
      corps de la notification affichée sur écran verrouillé

### FE-PUSH-3 — Préférences (optionnel)
Statut : ☐ non commencé — optionnel, à discuter avant de lancer
Commande : `/fe-push-preferences-ui`
- [ ] Écran de préférences pour activer/désactiver des catégories de
      notifications

## Hors scope explicite
- **OTP jamais envoyé par push** — reste SMS/email, c'est un mécanisme de
  livraison de code, pas une notification.
- **Newsletter jamais en push** — reste email opt-in, canal marketing
  distinct des notifications transactionnelles.
