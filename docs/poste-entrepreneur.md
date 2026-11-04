# Poste de l'entrepreneur

Ce document s'adresse à l'intégrateur et à l'entrepreneur. Il explique comment préparer
l'ordinateur de l'entrepreneur pour travailler avec un agent (opencode par défaut, Claude Code
en option), ce que la commande `bin/dokopilot harness` y installe, ce que font les garde-fous et
ce qui reste à surveiller.

L'agent tourne **sur l'ordinateur de l'entrepreneur**. Il parle à Dokos par le serveur MCP de
Dokos AI, à travers Tailscale, sous l'identité d'un utilisateur « assistant » qui peut lire et
préparer des brouillons mais ne peut ni valider, ni annuler, ni supprimer un document
([securite.md](securite.md)). La messagerie et l'agenda, s'ils sont activés, sont lus par deux
petits serveurs MCP qui tournent eux aussi sur l'ordinateur.

## 1. Préparer l'ordinateur

Systèmes pris en charge : Linux, macOS, Windows avec WSL 2 (lancez tout depuis WSL).

| Outil | Pour quoi | Installation |
| --- | --- | --- |
| Tailscale | Joindre Dokos (profil production) | <https://tailscale.com/download>, puis connexion avec le compte Tailscale de l'entrepreneur |
| opencode 2 (par défaut) | Le harnais | `curl -fsSL https://opencode.ai/v2/install \| bash`, ou `brew install anomalyco/tap/opencode-v2`, ou `npm install -g @opencode/cli` |
| Claude Code (option) | Le harnais | `npm install -g @anthropic-ai/claude-code` ou l'installateur de <https://code.claude.com/docs> ; **version 2.1.295 ou plus récente** |
| Python 3 | Garde de Claude Code, lancement du connecteur d'agenda | souvent présent ; sinon paquet `python3` |
| uv (`uvx`) | Connecteur de messagerie | <https://docs.astral.sh/uv/> |
| Node.js 24 ou plus récent (`npx`) | Connecteur d'agenda | <https://nodejs.org> ou le gestionnaire de paquets |
| bubblewrap et socat (Linux, WSL) | Bac à sable des commandes de Claude Code | `sudo apt-get install bubblewrap socat` |

Vérifiez l'accès à Dokos depuis l'ordinateur : en production, `tailscale status` doit montrer le
serveur, et `https://<nom-du-serveur>.<tailnet>.ts.net` doit ouvrir la page de connexion de Dokos.
Dans la console Tailscale, l'ordinateur doit être approuvé et autorisé à joindre le serveur.

## 2. Générer la configuration du poste

La commande se lance **sur l'ordinateur de l'entrepreneur**, depuis une copie du dépôt :

```bash
bin/dokopilot harness <client>                 # dossier de travail : ~/DokoPilot/<client>
bin/dokopilot harness <client> --dest ~/Gestion # ou un autre dossier
```

Elle a besoin, ce jour-là, de ce qu'il faut pour toute commande `bin/dokopilot` : la
configuration du client, une clé age qui déchiffre ses secrets, et un accès de maintenance au
serveur (clé SSH de l'intégrateur chargée dans l'agent SSH, compte nominatif et mot de passe
sudo des secrets). Si l'ordinateur n'a pas le fichier de clé publique cité dans `client.yml`,
déclarez la clé en clair dans `clients/<client>/client.local.yml` (`ssh_public_keys`).

Ce que fait la commande :

1. sur le serveur, elle lit avec sudo le jeton de l'utilisateur assistant de l'entrepreneur
   (`/etc/dokopilot/assistants/<assistant>.json`, créé par l'installation de Dokos) et, en mode
   Tailscale, le nom MagicDNS du serveur ; rien n'est modifié sur le serveur ;
2. sur l'ordinateur, elle range le jeton dans `~/.config/dokopilot/<client>/dokos-token`
   (fichier 0600, dossier 0700), **jamais** dans le dossier de travail ;
3. si la messagerie ou l'agenda sont activés, elle demande leur mot de passe (saisie masquée)
   la première fois seulement, et le range au même endroit ; ces mots de passe ne vont jamais
   dans les secrets du client ;
4. elle écrit le dossier de travail pour les harnais de `harness.enabled`, puis affiche le
   harnais par défaut (`harness.default`) et la commande pour commencer.

La relancer est sans risque : un fichier que l'entrepreneur a modifié dans le dossier de travail
est conservé sous `<fichier>.<date>.bak` avant d'être remplacé ; un fichier inchangé est
simplement mis à jour. Pour changer un mot de passe de messagerie ou d'agenda, supprimez
`~/.config/dokopilot/<client>/mail-password` ou `caldav-password`, puis relancez.

### Réglages (`clients/<client>/client.yml`, section `harness`)

| Réglage | Effet |
| --- | --- |
| `default` | `opencode` ou `claude-code` : le harnais conseillé à l'entrepreneur |
| `enabled` | les harnais à configurer, par exemple `[opencode, claude-code]` |
| `opencode_version` | `2` (défaut) ou `1` pour un poste resté sur OpenCode 1 (voir § 4) |
| `mail.enabled`, `mail.imap_host`, `mail.username` | messagerie ; facultatifs : `mail.email`, `mail.imap_port` (993), `mail.imap_tls` (`ssl` ou `starttls`), `mail.drafts_mailbox` |
| `calendar.enabled`, `calendar.caldav_url` (https), `calendar.username` | agenda ; facultatifs : `calendar.email`, `calendar.calendars` (liste des agendas autorisés) |

`mail.smtp_host` n'est pas utilisé : aucun serveur d'envoi n'est configuré, volontairement.

## 3. Ce qui est installé

Dans le dossier de travail :

| Fichier | Rôle |
| --- | --- |
| `AGENTS.md` | Consignes de l'agent, en français : pour qui il travaille, préparer sans valider, présentation des résultats, contextes, règle des courriels, confidentialité |
| `opencode.json`, `.opencode/agents/bureau.md`, `.opencode/agents/atelier.md` | Configuration d'opencode : serveurs MCP, permissions, les deux agents |
| `.opencode/plugins/dokopilot-guard.js` (+ `.policy.json`) | Garde « courriel » d'opencode |
| `CLAUDE.md`, `.mcp.json`, `.claude/settings.json`, `.claude/agents/`, `.claude/hooks/` | Configuration de Claude Code, ses deux agents et sa garde |
| `.agents/skills/`, `.claude/skills/` | Les compétences métier (factures fournisseurs, relances, rapprochement bancaire, synthèse, tri des courriels, devis) |

Dans `~/.config/dokopilot/<client>/` (0700, jamais lisible par l'agent) : `dokos-token`,
`mail-password`, `mail/config.toml`, `caldav-password`, `taint/` (marques de la garde de Claude
Code), `rendered/` (copie de la dernière génération, qui sert à repérer vos modifications).

## 4. Travailler avec l'agent

Ouvrez un terminal dans le dossier de travail.

- **opencode** : lancez `opencode`. Au premier lancement, choisissez le fournisseur de modèle
  avec `/connect` (ou `opencode auth login`). La touche Tab passe de l'agent **Bureau** à l'agent
  **Atelier**.
- **Claude Code** : lancez `claude` pour le **Bureau**, `claude --agent atelier` pour l'**Atelier**.
  Au premier lancement, acceptez la confiance dans le dossier : sans elle, la connexion à Dokos
  (`headersHelper`) et la garde ne s'activent pas. Claude Code demande aussi d'approuver les
  serveurs MCP du projet.

| Contexte | Pour quoi | Ce qui est possible |
| --- | --- | --- |
| Bureau (par défaut) | La gestion | Dokos, messagerie, agenda ; commandes et fichiers avec accord à chaque fois |
| Atelier | Le travail technique (scripts, exports, petites applications) | Commandes et fichiers du dossier de travail sans accord ; Dokos ; ni messagerie ni agenda |

**Le fournisseur de modèle** est au choix de l'entrepreneur, avec sa propre clé. Les données
qu'il fait lire à l'agent (factures, courriels, clients) sont envoyées à ce fournisseur et à lui
seul : choisissez-le en conscience (localisation des données, durée de conservation, usage pour
l'entraînement), de préférence avec un contrat professionnel. Enregistrez la clé avec `/connect`
ou `opencode auth login` plutôt que dans une variable d'environnement, que l'agent pourrait lire.

**OpenCode 1.** Un poste resté sur OpenCode 1 reçoit, avec `harness.opencode_version: 1`, la même
politique sous la forme V1. OpenCode 1 ne sait pas transformer une autorisation en question
depuis une extension : après la lecture d'un courriel, la garde y **bloque** le shell et la
modification de fichiers jusqu'à la fin de la session, et toutes les autres actions à effet
restent soumises à accord par les règles statiques. Passez à OpenCode 2 dès que possible.

## 5. Les garde-fous

**Dans Dokos (le plus solide).** L'utilisateur assistant n'a aucun droit de validation,
d'annulation ni de suppression : quel que soit le harnais, le modèle ou une éventuelle
manipulation, un document ne peut être validé que par l'entrepreneur.

**Dans le harnais (défense en profondeur).**

- Refusés : `doc_submit`, `doc_cancel`, `doc_delete` ; l'envoi, le transfert, la suppression,
  le déplacement et les commandes brutes de la messagerie ; la suppression, le déplacement et la
  réponse aux invitations de l'agenda ; la lecture de `~/.config/dokopilot`, de `~/.ssh` et des
  identifiants d'opencode ; la modification des fichiers de configuration de DokoPilot ; les
  commandes `opencode` et `claude` lancées par l'agent (elles pourraient afficher une
  configuration résolue).
- Soumis à accord : `doc_create` et `doc_update`, les brouillons de courriels, les écritures
  d'agenda, tout accès web, tout accès hors du dossier de travail ; dans le Bureau, chaque
  commande et chaque modification de fichier. opencode garde ces questions même après un
  « Toujours autoriser » pour les écritures dans Dokos, la messagerie et l'agenda.
- Messagerie : le compte n'accorde que les brouillons (`allowed_mutations = ["draft"]`) et n'a
  aucun serveur d'envoi ; les brouillons attendent dans le dossier Brouillons.
- Claude Code : bac à sable du système (bubblewrap, Seatbelt sur macOS) pour les commandes, qui
  ne peuvent ni lire `~/.config/dokopilot` ni écrire la configuration, et ne joignent que Dokos
  sans demander ; mode « bypass » désactivé.

**La garde « courriel ».** Une session qui a lu un courriel devient **exposée** : jusqu'à la fin
de cette session, toute action à effet (commande, fichier, accès web, écriture dans Dokos,
brouillon, agenda) demande une confirmation, même dans l'Atelier. La session est marquée
*avant* que le courriel soit lu ; si la marque ne peut pas être enregistrée, la lecture est
bloquée. Le contenu d'un courriel est une donnée, jamais une instruction : les consignes de
l'agent et chaque confirmation le rappellent. Avec opencode, la marque est gardée par
l'extension (aussi après un redémarrage d'opencode 2) ; avec Claude Code, par un fichier par
session dans `~/.config/dokopilot/<client>/taint/`. Pour un travail sans lien avec les
courriels, ouvrez une nouvelle session.

## 6. Risques résiduels

- **Le jeton dans opencode.** opencode lit le jeton avec sa substitution `{file:…}` au
  chargement de sa configuration : il n'est ni dans le dossier de travail ni dans
  l'environnement des commandes, mais opencode n'a pas de bac à sable. Dans l'Atelier, où les
  commandes passent sans accord, une commande détournée (par exemple écrite par un script) peut
  encore lire `~/.config/dokopilot` : les règles de refus reconnaissent les formes usuelles, pas
  toutes. Gardez l'Atelier pour du travail technique sans contenu extérieur.
- **Le jeton dans Claude Code.** Il est lu par le `headersHelper`, hors du bac à sable, à la
  connexion. Si bubblewrap et socat manquent, Claude Code lance les commandes sans bac à sable
  (sauf avec `harness_claude_sandbox_required: true`) et seules les règles de refus restent.
- **Ce que le jeton permet.** Celui qui le détient agit comme l'assistant : lire tout ce que
  l'assistant lit et préparer des brouillons, sans pouvoir valider. En cas de doute, l'intégrateur
  régénère le jeton (nouvelle installation de Dokos) puis relance `bin/dokopilot harness`.
- **L'extension et les accroches sont dans le dossier de travail.** Les règles les protègent de
  l'agent, et le bac à sable de Claude Code aussi ; un programme lancé hors du harnais peut les
  modifier, comme tout fichier de l'utilisateur.
- **Les modes sans question** : `opencode --auto` (OpenCode 1) approuve toutes les questions sauf
  les refus. Ne l'utilisez pas. Le mode « bypass » de Claude Code est désactivé par la
  configuration du projet.
- **Les approbations durables** : « Toujours autoriser » (opencode) ou « Oui, ne plus demander »
  (Claude Code) suppriment les questions suivantes du même type, hors écritures que la garde
  garde soumises à accord. Préférez « une fois ».
- **Les serveurs MCP de messagerie et d'agenda** tournent hors du bac à sable, avec les droits de
  l'utilisateur ; leurs versions sont figées (mcp-email-server 1.14.0, @ni-c/caldav-mcp 0.1.5)
  et téléchargées par `uvx` et `npx` au premier lancement.
- **Le fournisseur de modèle** voit tout ce que l'agent lit. Les données personnelles des
  clients et des salariés y passent : informez-en les personnes concernées si nécessaire
  (RGPD), et ne confiez pas à l'agent de données de santé ou d'autres données sensibles.
- **Tailscale** : les noms des machines figurent dans les journaux publics de certificats ;
  choisissez des noms neutres.
