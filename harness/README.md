<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Pack harnais

Ce dossier contient tout ce que `bin/dokopilot harness <client>` installe sur l'ordinateur de
l'entrepreneur. Le guide d'utilisation est [docs/poste-entrepreneur.md](../docs/poste-entrepreneur.md) ;
cette page décrit le pack pour ceux qui le modifient.

| Chemin | Contenu |
| --- | --- |
| `templates/AGENTS.md.j2` | Consignes de l'agent (français), communes à opencode et Claude Code |
| `templates/CLAUDE.md.j2` | `CLAUDE.md`, qui importe `AGENTS.md` |
| `templates/data.json.j2` | Sérialise les faits construits par `ansible/roles/harness/tasks/policy.yml` : `opencode.json`, `.mcp.json`, `.claude/settings.json`, politiques des deux gardes |
| `templates/opencode/` | Agents opencode `bureau` et `atelier` (permissions en forme V2, ou V1 convertie et vérifiée) |
| `templates/claude/` | Agents Claude Code `bureau` (session principale par défaut) et `atelier` |
| `templates/mail/config.toml.j2` | Compte de messagerie (mcp-email-server) : lecture et brouillons, sans serveur d'envoi |
| `opencode/plugins/dokopilot-guard.js` | Garde « courriel » d'opencode 2 (`permission.hook("evaluate")`), avec repli pour OpenCode 1 |
| `claude/hooks/mail_taint_guard.py` | Garde de Claude Code (PreToolUse et PostToolUse), Python standard, échec fermé |
| `claude/hooks/dokos_headers.py` | `headersHelper` du serveur MCP Dokos : lit le jeton dans son fichier privé |
| `claude/hooks/caldav_launcher.py` | Lance le serveur d'agenda avec son mot de passe lu dans un fichier privé |
| `skills/<nom>/SKILL.md` | Compétences métier (format Agent Skills), copiées dans `.agents/skills/` et `.claude/skills/` |

## Principes

- **Les décisions de sécurité sont des données**, au même endroit : listes d'outils et règles dans
  `ansible/roles/harness/defaults/main.yml`, assemblage dans `tasks/policy.yml`. Les gardes lisent
  une politique JSON générée à côté d'elles : pas de liste d'outils recopiée dans le code.
- **Aucun secret dans le dossier de travail.** Le jeton Dokos et les mots de passe restent dans
  `~/.config/dokopilot/<client>/` (0700) ; les configurations y font seulement référence.
- **Préparer, jamais valider** : les outils `doc_submit`, `doc_cancel`, `doc_delete` sont refusés
  dans les deux harnais, en plus du refus de Dokos lui-même.
- **Échec fermé** : une garde qui ne peut pas décider bloque ou demande.

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

`tests/harness/run.sh` génère le dossier de travail du client de démonstration contre un faux
serveur (variables `harness_assistant_credentials` et `harness_tailscale_dns_name`), puis vérifie
les fichiers JSON, l'absence de tout secret dans le dossier, les droits des fichiers privés, les
règles de refus, l'idempotence, la copie `.bak` d'un fichier modifié, le comportement de la garde
de Claude Code sur des exemples, et le format des compétences.
