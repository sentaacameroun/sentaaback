# Sentaa — Backend (Django / DRF)

## Contexte

Sentaa combine une marketplace P2P (paiement en escrow, livraison interne par
coursiers) et un module jobs (offres + candidatures). Le backend **existe déjà** :
Django 5 / DRF, découpage par domaine (une app = un domaine), couches
models/serializers/views/services/permissions globalement respectées.

Un audit complet du backend est disponible dans `docs/AUDIT_BACKEND.md`. Lis-le
(ou la section pertinente) avant toute intervention non triviale — ne devine pas
l'état du code, vérifie-le.

**Verdict de l'audit : refactor progressif, pas de réécriture.** Le refactor est
découpé en 4 PR indépendantes et livrables séparément, trackées dans
`REFACTOR_PLAN.md`. Ne travaille jamais hors du scope de la PR en cours — si une
correction hors scope te semble nécessaire, note-la dans `REFACTOR_PLAN.md` au
lieu de la faire "en passant".

## Stack

- Django 5.0 / DRF 3.16, PostgreSQL, Celery + Redis, Channels (WebSocket),
  Cloudinary (images), NotchPay (Mobile Money)
- Tests : `pytest` (pytest-django) — lint : `ruff check .` / `black --check .`
- Pas de données de production à préserver actuellement : une migration
  destructive locale est acceptable, mais demande toujours confirmation avant
  de l'exécuter (voir `.claude/settings.json`, `migrate` est en `ask`).

## Règles non négociables (tout le projet)

1. **Argent = `Decimal`, jamais `float`.** Tout champ monétaire est un
   `DecimalField`.
2. **Toute opération qui modifie des fonds (escrow, payout) doit être
   atomique** (`transaction.atomic` + `select_for_update`) **et idempotente**
   (vérifiable via une contrainte unique, pas seulement une garde applicative).
3. **Un seul chemin de code peut clôturer une commande et libérer les fonds.**
   Ne jamais dupliquer cette logique entre `escrow` et `logistics`.
4. **Permissions objet obligatoires** sur tout ViewSet exposant
   update/destroy : compare explicitement `request.user` au champ propriétaire
   de l'objet. `IsAuthenticated` seul ne suffit jamais.
5. **Aucun verbe DELETE sur les commandes** (`Order`), ni sur tout ce qui a une
   FK vers `PaymentTransaction`. Le journal financier ne doit jamais pouvoir
   être supprimé, même par cascade.
6. **Tout changement dans `escrow/` ou `logistics/`** touchant à un statut, un
   paiement ou un payout doit être accompagné d'un test qui échoue sans le
   correctif.
7. Ne jamais réintroduire un champ monétaire modifiable par le client dans un
   serializer (ex. `shipping_fee`) sans le rendre `read_only` et calculé côté
   serveur.

Ces règles sont détaillées et scopées par app dans `.claude/rules/` — elles se
chargent automatiquement quand tu travailles dans les fichiers concernés.

## Où regarder

- Détail complet de l'état des lieux : `docs/AUDIT_BACKEND.md` (référence à
  lire à la demande — ne pas la relire en entier à chaque session, cible la
  section utile)
- Plan de refactor + statut des 4 PR : `REFACTOR_PLAN.md`
- Commandes pour avancer PR par PR : `/refactor-pr1-security`,
  `/refactor-pr2-ledger`, `/refactor-pr3-lifecycle`, `/refactor-pr4-cleanup`
- Revue dédiée avant de clore une modif du cycle de vie escrow : subagent
  `escrow-reviewer`

## Workflow attendu

1. Avant de commencer une PR, lis son entrée dans `REFACTOR_PLAN.md` (scope,
   fichiers autorisés, critères d'acceptation) — invoque la commande dédiée
   plutôt que de partir d'un prompt libre.
2. Ne touche pas aux fichiers hors du scope listé pour la PR en cours.
3. Après un changement sur `escrow/` ou `logistics/`, lance les tests de ces
   apps avant de considérer la tâche terminée.
4. Pour toute modification du cycle de vie de la commande (PR 3), fais
   revoir le diff par le subagent `escrow-reviewer` avant de clore.
5. Mets à jour la case correspondante dans `REFACTOR_PLAN.md` une fois la PR
   terminée et les tests verts. Ne coche jamais une case si `pytest` échoue.

## Commandes utiles

- Tests : `pytest`
- Lint : `ruff check .` / `black --check .`
- Migrations : `python manage.py makemigrations` puis `python manage.py
  migrate` (⚠️ `migrate` demande confirmation par défaut, voir
  `.claude/settings.json`)
