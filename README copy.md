# Config Claude Code — refactor backend Sentaa

## Installation

1. Dézippe ce dossier **à la racine de ton repo backend** (là où se trouve déjà
   `manage.py`). Les chemins sont conçus pour fusionner avec ce qui existe :

```
sentaa-backend/
├── manage.py                  (déjà présent)
├── escrow/, marketplace/, ... (déjà présents)
├── CLAUDE.md                  ← nouveau
├── REFACTOR_PLAN.md           ← nouveau
├── docs/
│   └── AUDIT_BACKEND.md       ← nouveau
└── .claude/
    ├── settings.json          ← nouveau
    ├── rules/
    │   ├── escrow-core.md
    │   ├── security.md
    │   └── testing.md
    ├── skills/
    │   ├── refactor-pr1-security/SKILL.md
    │   ├── refactor-pr2-ledger/SKILL.md
    │   ├── refactor-pr3-lifecycle/SKILL.md
    │   └── refactor-pr4-cleanup/SKILL.md
    └── agents/
        └── escrow-reviewer.md
```

2. Si tu as déjà un `.claude/settings.json` avec des règles perso, fusionne-le
   à la main plutôt que d'écraser — les clés `permissions.allow/ask/deny`
   s'additionnent.

3. Commit tout ça dans git. `CLAUDE.md`, `.claude/rules/`, `.claude/skills/`,
   `.claude/agents/`, `REFACTOR_PLAN.md` et `docs/AUDIT_BACKEND.md` sont faits
   pour être partagés avec toute personne qui reprendrait le projet (toi y
   compris, dans 3 mois).

## Comment ça évite que Claude se perde

- **`CLAUDE.md`** est volontairement court (~90 lignes) : les règles
  génériques (Decimal, atomicité, permissions objet) sont dedans, chargées à
  chaque session. Le détail lourd (l'audit complet) n'est PAS importé —
  Claude va le lire à la demande via `docs/AUDIT_BACKEND.md`, donc il ne
  pèse rien tant qu'on n'en a pas besoin.
- **`.claude/rules/escrow-core.md` et `security.md`** ont un `paths:` en
  frontmatter : ils ne se chargent que quand Claude touche réellement à
  `escrow/`, `logistics/`, `marketplace/`, `jobs/` ou `users/`. Pas de bruit
  de contexte sur le reste du projet (chat, companies, notifications...).
- **Les 4 skills `refactor-prX-*`** ont `disable-model-invocation: true` :
  Claude ne les déclenche jamais tout seul. Tu tapes `/refactor-pr1-security`
  quand tu veux commencer cette PR précise, et le skill fixe explicitement
  le scope de fichiers autorisés — ça empêche Claude de dériver vers du
  refactor hors sujet au milieu d'une tâche.
- **`REFACTOR_PLAN.md`** est la source de vérité de l'avancement. Demande à
  Claude de le lire en début de session ("où on en est sur le refactor ?")
  plutôt que de repartir d'un prompt libre à chaque fois.
- **Le subagent `escrow-reviewer`** est en lecture seule (pas d'Edit/Write) :
  il sert de deuxième paire d'yeux sur les PR touchant à l'argent sans
  pouvoir lui-même modifier quoi que ce soit — utile car c'est la seule zone
  où une erreur peut coûter cher.
- **`.claude/settings.json`** met `migrate`, `flush`, un `docker compose down`
  et tout `git push` en confirmation obligatoire, et bloque la lecture de
  `.env` par les outils de Claude. Comme il n'y a pas de données de prod à
  préserver, une remise à zéro locale est acceptable — mais elle reste un
  geste qu'on valide, pas un réflexe automatique de l'agent.

## Usage recommandé

```
# Démarrer une PR
/refactor-pr1-security

# Une fois PR 1 verte et cochée dans REFACTOR_PLAN.md
/refactor-pr2-ledger

# Idem pour PR 3, puis PR 4

# Pendant PR 3, avant de clore une tâche sur le cycle de vie de la commande :
Fais revoir ce diff par le subagent escrow-reviewer avant qu'on continue.
```

Si tu préfères travailler à l'ancienne (prompt libre), les règles dans
`.claude/rules/` et `CLAUDE.md` s'appliquent quand même — les skills sont un
garde-fou supplémentaire, pas une obligation.
