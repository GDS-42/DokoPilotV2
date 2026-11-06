<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Pack harnais

Ce dossier contient tout ce que `bin/dokopilot harness <client> [--person <id>]` installe sur
l'ordinateur d'une personne, **dans sa copie du dépôt de contexte** `<client>/entreprise`. Le
guide d'utilisation est [docs/poste-entrepreneur.md](../docs/poste-entrepreneur.md) ; cette page
décrit le pack pour ceux qui le modifient. Le contrat est la section C-4 de
[docs/plan-v0.2.md](../docs/plan-v0.2.md).

| Chemin | Contenu |
| --- | --- |
| `templates/data.json.j2` | Sérialise les faits construits par `ansible/roles/harness/tasks/policy.yml` : `opencode.json`, `.mcp.json`, `.claude/settings.json`, politiques des deux gardes |
| `templates/opencode/` | Agents opencode `bureau` et `atelier` (modèle de chaque agent, permissions en forme V2, ou V1 convertie et vérifiée) |
| `templates/claude/` | Agents Claude Code `bureau` (session principale par défaut) et `atelier` |
| `templates/mail/config.toml.j2` | Compte de messagerie (mcp-email-server) : lecture et brouillons, sans serveur d'envoi |
| `opencode/plugins/dokopilot-guard.js` | Garde « courriel » d'opencode 2 (`permission.hook("evaluate")`), avec repli pour OpenCode 1 ; donne au shell de l'agent l'identité Git de l'assistant |
| `claude/hooks/mail_taint_guard.py` | Garde de Claude Code (PreToolUse et PostToolUse), Python standard, échec fermé |
| `claude/hooks/dokos_headers.py` | `headersHelper` du serveur MCP Dokos : lit le jeton dans son fichier privé |
| `claude/hooks/caldav_launcher.py` | Lance le serveur d'agenda avec son mot de passe lu dans un fichier privé |
| `claude/hooks/model_key_helper.py` | `apiKeyHelper` de Claude Code (`models.claude_code_auth: api_key`) : lit la clé dans son fichier privé |
| `skills/dokopilot-<nom>/SKILL.md` | Procédures de DokoPilot (format Agent Skills), copiées dans `.agents/skills/` et `.claude/skills/` |

`AGENTS.md` et `CLAUDE.md` ne sont plus générés par le harnais : ils appartiennent au dépôt
`entreprise`, déposé une fois par le rôle `context` (modèles dans [`context/`](../context/)).
Le clonage, la mise à jour et l'import sont faits par `ansible/roles/harness/files/workspace_git.py`.

## Principes

- **Les décisions de sécurité sont des données**, au même endroit : listes d'outils et règles dans
  `ansible/roles/harness/defaults/main.yml`, assemblage dans `tasks/policy.yml`. Les gardes lisent
  une politique JSON générée à côté d'elles : pas de liste d'outils recopiée dans le code.
- **Aucun secret dans le dossier de travail.** Le jeton Dokos, le jeton Git, le mot de passe
  Forgejo, la clé du modèle et les mots de passe restent dans `~/.config/dokopilot/<client>/`
  (0700) ; les configurations y font seulement référence. Aucun jeton dans une adresse de dépôt
  ni dans `.git/config`.
- **Les fichiers du harnais n'entrent jamais dans Git** : `.gitignore` du dépôt et, en plus, un
  bloc géré dans `.git/info/exclude` de la copie.
- **L'agent prépare, la personne valide et publie** : `doc_submit`, `doc_cancel`, `doc_delete`
  refusés ; écriture libre dans `00-Boite-de-reception/` et `perso/` seulement ; `git push`
  refusé, la personne envoie avec `bin/dokopilot sync`.
- **Échec fermé** : une garde qui ne peut pas décider bloque ou demande ; une copie dont la
  configuration Git contient un réglage inattendu arrête la commande.

## Versions figées

| Composant | Version | Où la changer |
| --- | --- | --- |
| mcp-email-server | 1.14.0 | `harness_mail_mcp_package` |
| @ni-c/caldav-mcp | 0.1.5 | `harness_calendar_mcp_package` |
| opencode | 2.x (1.x en repli) | `harness.opencode_version` du client |
| Claude Code | 2.1.295 ou plus récent | `harness_claude_code_min_version` (documentation) |

Avant de changer une version, relisez la liste des outils du serveur (noms, outils d'envoi ou de
suppression) et mettez à jour les listes de `defaults/main.yml`.

## Tests

`tests/harness/run.sh` installe le harnais du client de démonstration contre un faux serveur
(fichiers d'identifiants lus sans sudo) et un faux Forgejo (`tests/harness/fake_forgejo.py` :
dépôts nus remplis depuis `context/`, servis en HTTP par `git http-backend` avec authentification
par jeton), pour le dirigeant (réseau local et Tailscale, OpenCode 2 et 1) et pour un membre (sans
Dokos ni `direction/`). Il vérifie les copies et leur configuration, l'absence de tout secret dans
le dossier et dans l'historique Git, les droits des fichiers privés, les règles de permission par
chemin, les modèles et `small_model`, les procédures renommées, l'import, l'idempotence, la copie
`.bak` d'un fichier modifié, les messages d'erreur, le comportement des deux gardes sur des
exemples (`tests/harness/plugin_check.mjs` pour opencode) et l'identité des commits de l'agent.
