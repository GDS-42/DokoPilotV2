# Poste de travail d'une personne

Ce document s'adresse à l'intégrateur et aux personnes de l'entreprise. Il explique comment
préparer l'ordinateur d'une personne pour travailler avec un agent (opencode par défaut, Claude
Code en option), ce que la commande `bin/dokopilot harness` y installe, ce que font les
garde-fous et ce qui reste à surveiller.

L'agent tourne **sur l'ordinateur de la personne**, dans son **dossier de travail**, qui est sa
copie du dépôt de contexte de l'entreprise (Forgejo, sur le serveur, joignable seulement par le
réseau privé). Il agit au nom de l'**assistant** de la personne : ses commits sont signés
« Assistant de <Prénom> », et il ne publie jamais rien lui-même : c'est la personne qui envoie
les changements avec `bin/dokopilot sync`.

- **Le dirigeant** : son agent parle aussi à Dokos par le serveur MCP de Dokos AI, sous
  l'identité d'un utilisateur « assistant » qui peut lire et préparer des brouillons mais ne
  peut ni valider, ni annuler, ni supprimer un document ([securite.md](securite.md)). Il a aussi
  la copie du dépôt `direction` (confidentiel), et la messagerie et l'agenda s'ils sont activés.
- **Un membre** : en version 0.2, son agent n'a **pas** accès à Dokos (décision D-29) ; il
  travaille avec le contexte de l'entreprise et son contexte personnel. Le dirigeant crée dans
  Dokos les comptes de ses salariés, avec les rôles qui conviennent.

## 1. Préparer l'ordinateur

Systèmes pris en charge : Linux, macOS, Windows avec WSL 2 (lancez tout depuis WSL).

| Outil | Pour quoi | Installation |
| --- | --- | --- |
| Git | Les copies du contexte | paquet `git` |
| Tailscale | Joindre Dokos et Forgejo (profil production) | <https://tailscale.com/download>, puis connexion avec le compte Tailscale de l'entreprise |
| opencode 2 (par défaut) | Le harnais | `curl -fsSL https://opencode.ai/v2/install \| bash`, ou `brew install anomalyco/tap/opencode-v2`, ou `npm install -g @opencode/cli` |
| Claude Code (option) | Le harnais | `npm install -g @anthropic-ai/claude-code` ou l'installateur de <https://code.claude.com/docs> ; **version 2.1.295 ou plus récente** |
| Python 3 | Gardes de Claude Code, lancement du connecteur d'agenda | souvent présent ; sinon paquet `python3` |
| uv (`uvx`) | Connecteur de messagerie (dirigeant) | <https://docs.astral.sh/uv/> |
| Node.js 24 ou plus récent (`npx`) | Connecteur d'agenda (dirigeant) | <https://nodejs.org> ou le gestionnaire de paquets |
| bubblewrap et socat (Linux, WSL) | Bac à sable des commandes de Claude Code | `sudo apt-get install bubblewrap socat` |

Vérifiez l'accès au serveur depuis l'ordinateur : en production, `tailscale status` doit montrer
le serveur, `https://<nom-du-serveur>.<tailnet>.ts.net` doit ouvrir la page de connexion de
Dokos et `https://<nom-du-serveur>.<tailnet>.ts.net:8443` celle de Forgejo (en démonstration :
`http://<adresse>` et `http://<adresse>:3000`). Dans la console Tailscale, l'ordinateur doit
être approuvé et autorisé à joindre le serveur.

## 2. Générer la configuration du poste

La commande se lance **sur l'ordinateur de la personne**, depuis une copie du dépôt DokoPilot,
une fois le serveur installé (`bin/dokopilot install`, qui crée les comptes Forgejo et dépose le
contexte) :

```bash
bin/dokopilot harness <client>                   # le dirigeant ; dossier : ~/DokoPilot/<client>
bin/dokopilot harness <client> --person lucas    # un membre (id de users.people)
bin/dokopilot harness <client> --dest ~/Gestion  # un autre dossier de travail
```

Elle a besoin, ce jour-là, de ce qu'il faut pour toute commande `bin/dokopilot` : la
configuration du client, une clé age qui déchiffre ses secrets, et un accès de maintenance au
serveur (clé SSH de l'intégrateur chargée dans l'agent SSH, compte nominatif et mot de passe
sudo des secrets). Si l'ordinateur n'a pas le fichier de clé publique cité dans `client.yml`,
déclarez la clé en clair dans `clients/<client>/client.local.yml` (`ssh_public_keys`).

Ce que fait la commande :

1. **Sur le serveur**, avec sudo, sans rien y modifier, elle lit les identifiants Forgejo de la
   personne (`/etc/dokopilot/forgejo/<id>.json`), ceux de l'assistant dans Dokos pour le
   dirigeant seulement (`/etc/dokopilot/assistants/<assistant>.json`), l'adresse de Forgejo et,
   en mode Tailscale, le nom MagicDNS du serveur. Ce qui n'existe pas pour cette personne est
   sauté, avec un message.
2. **Sur l'ordinateur**, elle range dans `~/.config/dokopilot/<client>/` (dossier 0700, fichiers
   0600, **jamais** dans le dossier de travail) : le jeton Git de la personne
   (`git-credentials`), son mot de passe Forgejo initial (`forgejo-password`), le jeton Dokos de
   l'assistant (`dokos-token`, dirigeant seulement) et la clé du fournisseur de modèle
   (`model-api-key`, demandée si l'installateur ne l'a pas déjà écrite).
3. Elle **copie** (ou met à jour) les dépôts en HTTPS : `entreprise` devient le dossier de
   travail, `direction` va dans `direction/` (dirigeant seulement), `perso-<id>` dans `perso/`.
   Chaque copie reçoit le nom et l'adresse de la personne (`user.name`, `user.email`) et
   l'assistant d'identifiants de Git qui lit `git-credentials` (`credential.helper "store --file
   ~/.config/dokopilot/<client>/git-credentials"`) : aucun jeton dans l'adresse du dépôt ni dans
   `.git/config`. Une copie déjà là n'est mise à jour que si elle est propre, et seulement en
   avance rapide (`git pull --ff-only`) ; sinon un message l'explique.
4. Si la messagerie ou l'agenda sont activés (dirigeant), elle demande leur mot de passe (saisie
   masquée) la première fois seulement, et le range au même endroit.
5. Elle écrit les fichiers du harnais **dans la copie** `entreprise` (tous ignorés par Git), puis,
   si `context.import_path` est renseigné, importe ce dossier une fois (voir plus bas).
6. Elle affiche le harnais par défaut, la commande pour commencer et ce qui a été sauté.

La relancer est sans risque : un fichier du harnais que la personne a modifié est conservé sous
`<fichier>.<date>.bak` avant d'être remplacé ; un fichier inchangé est simplement mis à jour ;
`AGENTS.md`, `CLAUDE.md` et tout le contenu du dépôt ne sont jamais touchés. Pour changer un mot
de passe de messagerie ou d'agenda, ou la clé du modèle, supprimez le fichier correspondant de
`~/.config/dokopilot/<client>/`, puis relancez.

**Depuis la version 0.1.** L'ancien dossier `~/DokoPilot/<client>` n'est pas une copie Git :
la commande refuse de s'en servir. Déplacez-le (`mv ~/DokoPilot/<client> ~/DokoPilot/<client>.ancien`),
relancez, puis reprenez-y ce qui vous sert (scripts, notes) en le rangeant dans la boîte de
réception.

### Réglages (`clients/<client>/client.yml`)

| Réglage | Effet |
| --- | --- |
| `harness.default` | `opencode` ou `claude-code` : le harnais conseillé |
| `harness.enabled` | les harnais à configurer, par exemple `[opencode, claude-code]` |
| `harness.opencode_version` | `2` (défaut) ou `1` pour un poste resté sur OpenCode 1 (voir § 4) |
| `harness.mail.enabled`, `.imap_host`, `.username` | messagerie du dirigeant ; facultatifs : `mail.email`, `mail.imap_port` (993), `mail.imap_tls` (`ssl` ou `starttls`), `mail.drafts_mailbox` |
| `harness.calendar.enabled`, `.caldav_url` (https), `.username` | agenda du dirigeant ; facultatifs : `calendar.email`, `calendar.calendars` (liste des agendas autorisés) |
| `models.provider` | fournisseur de modèle (identifiant opencode : `anthropic`, `mistral`, `openai`, `ollama`…) |
| `models.bureau`, `models.atelier` | modèle de chaque agent, `<fournisseur>/<modèle>` |
| `models.small` | modèle des titres et résumés ; vide : celui du Bureau |
| `models.claude_code_auth` | `subscription` (connexion avec `/login`) ou `api_key` (clé Anthropic de `model-api-key`) |
| `context.import_path` | dossier de ce poste à importer une fois dans la boîte de réception |

`harness.mail.smtp_host` n'est pas utilisé : aucun serveur d'envoi n'est configuré, volontairement.
La messagerie et l'agenda décrivent les comptes du dirigeant : ils ne sont pas configurés sur le
poste d'un membre en version 0.2.

## 3. Ce qui est installé

Le dossier de travail (`~/DokoPilot/<client>/`) :

| Chemin | Rôle | Dans Git |
| --- | --- | --- |
| `AGENTS.md`, `CLAUDE.md`, `00-…` à `90-…`, `.agents/skills/<nom>/` | Le contexte de l'entreprise et ses règles (dépôt `entreprise`) | oui, dépôt `entreprise` |
| `direction/` | Copie du dépôt `direction` (dirigeant seulement, confidentiel) | dépôt `direction` |
| `perso/` | Copie du dépôt `perso-<id>` : préférences, mémoire vive, brouillons | dépôt `perso-<id>` |
| `opencode.json`, `.opencode/agents/`, `.opencode/plugins/` | Configuration d'opencode : serveurs MCP, modèles, permissions, les deux agents, la garde | non (ignoré) |
| `.mcp.json`, `.claude/settings.json`, `.claude/agents/`, `.claude/hooks/` | Configuration de Claude Code, ses deux agents et ses gardes | non (ignoré) |
| `.agents/skills/dokopilot-…/`, `.claude/skills/dokopilot-…/` | Les procédures de DokoPilot (factures fournisseurs, relances, rapprochement bancaire, synthèse, tri des courriels, devis) | non (ignoré) |

Les procédures propres à l'entreprise vivent à côté, dans `.agents/skills/<nom>/`, versionnées
dans le dépôt `entreprise` ; DokoPilot ne les écrase jamais. Les fichiers du harnais sont
ignorés deux fois : par le `.gitignore` du dépôt et par un bloc « DokoPilot » de
`.git/info/exclude`, propre à la copie.

Dans `~/.config/dokopilot/<client>/` (0700, jamais lisible par l'agent) : `git-credentials`,
`forgejo-password`, `model-api-key`, `dokos-token` (dirigeant), `mail-password`,
`mail/config.toml`, `caldav-password`, `imports.json` (imports déjà faits), `taint/` (marques de
la garde de Claude Code), `rendered/` (copie de la dernière génération, qui sert à repérer vos
modifications).

**Le mot de passe Forgejo initial** (`forgejo-password`) sert à se connecter à l'interface web
de Forgejo pour relire l'historique ; changez-le dans les réglages de Forgejo. Le jeton Git, lui,
ne dépend pas du mot de passe.

## 4. Travailler avec l'agent

Ouvrez un terminal dans le dossier de travail.

- **opencode** : lancez `opencode`. La touche Tab passe de l'agent **Bureau** à l'agent
  **Atelier**. Si `models.*` est vide, choisissez le fournisseur avec `/connect` et le modèle
  avec `/models`.
- **Claude Code** : lancez `claude` pour le **Bureau**, `claude --agent atelier` pour l'**Atelier**.
  Au premier lancement, acceptez la confiance dans le dossier : sans elle, la connexion à Dokos
  (`headersHelper`), la clé du modèle (`apiKeyHelper`) et la garde ne s'activent pas. Claude Code
  demande aussi d'approuver les serveurs MCP du projet. Avec un abonnement Claude
  (`models.claude_code_auth: subscription`), connectez-vous avec `/login`.

| Contexte | Pour quoi | Ce qui est possible |
| --- | --- | --- |
| Bureau (par défaut) | La gestion | Contexte, Dokos, messagerie, agenda (selon la personne) ; commandes avec accord à chaque fois ; fichiers libres dans la boîte de réception et `perso/`, sur accord ailleurs |
| Atelier | Le travail technique (scripts, exports, petites applications) | Commandes du dossier de travail sans accord ; mêmes règles pour les fichiers ; ni messagerie ni agenda |

**Les modèles.** Chaque agent a son modèle (`models.bureau`, `models.atelier`) et les petites
tâches (titres, résumés) utilisent `models.small` : ainsi, aucun modèle par défaut hébergé par un
tiers ne voit les sessions (décision D-23). opencode lit la clé du fournisseur dans
`~/.config/dokopilot/<client>/model-api-key` au chargement de sa configuration (`{file:…}`).
Les données que l'agent lit (factures, courriels, clients, notes) sont envoyées à ce fournisseur
et à lui seul : choisissez-le en conscience (localisation des données, durée de conservation,
usage pour l'entraînement), de préférence avec un contrat professionnel. Ne mettez jamais la clé
dans une variable d'environnement, que l'agent pourrait lire.

**Enregistrer et envoyer.** L'agent commite dans les copies, au nom de « Assistant de <Prénom> »
(un commit par intention, message en français) ; il ne pousse jamais. La personne relit
(`git log -p`, ou l'interface de Forgejo après envoi) puis envoie tout avec :

```bash
bin/dokopilot sync <client> [--person <id>]
```

**Importer un dossier existant** (un coffre Obsidian, par exemple) : renseignez
`context.import_path`, puis relancez `bin/dokopilot harness`. Le contenu est copié une seule fois
dans `00-Boite-de-reception/import-<date>/`, sans liens symboliques, sans dossiers `.git` ni
`.obsidian`, sans fichier exécutable, et enregistré dans Git **sur ce poste seulement**. Un
document importé est une donnée, jamais une instruction (décision D-27) : triez-le avec l'agent,
relisez, puis envoyez avec `bin/dokopilot sync`.

**OpenCode 1.** Un poste resté sur OpenCode 1 reçoit, avec `harness.opencode_version: 1`, la même
politique sous la forme V1 (et `small_model`). OpenCode 1 ne sait pas transformer une
autorisation en question depuis une extension : après la lecture d'un courriel, la garde y
**bloque** le shell et la modification de fichiers jusqu'à la fin de la session, et toutes les
autres actions à effet restent soumises à accord par les règles statiques. Passez à OpenCode 2
dès que possible.

## 5. Les garde-fous

**Dans Dokos (le plus solide).** L'utilisateur assistant n'a aucun droit de validation,
d'annulation ni de suppression : quel que soit le harnais, le modèle ou une éventuelle
manipulation, un document ne peut être validé que par une personne.

**Dans Forgejo.** Le jeton de la personne n'ouvre que ses dépôts (`entreprise`, `perso-<id>`, et
`direction` pour le dirigeant) ; un membre ne peut ni lire `direction` ni écrire dans le `perso`
d'un autre.

**Dans le harnais (défense en profondeur).**

- Écriture des fichiers : **libre** dans `00-Boite-de-reception/` et `perso/` ; **sur accord**
  partout ailleurs dans le dossier de travail, et toujours pour `direction/` et pour les fichiers
  de consignes (`AGENTS.md` à tous les niveaux) ; **refusée** dans `~/.config/dokopilot`, dans les
  dossiers `.git`, dans `CLAUDE.md` et dans la configuration du harnais.
- Refusés : `git push` (et les formes usuelles `git -C … push`), `git credential`, les
  changements de configuration Git (`git config`, `git remote`) ; `doc_submit`, `doc_cancel`,
  `doc_delete` ; l'envoi, le transfert, la suppression, le déplacement et les commandes brutes de
  la messagerie ; la suppression, le déplacement et la réponse aux invitations de l'agenda ; la
  lecture de `~/.config/dokopilot`, de `~/.ssh` et des identifiants d'opencode ; les commandes
  `opencode` et `claude` lancées par l'agent (elles pourraient afficher une configuration résolue).
- Soumis à accord : `doc_create` et `doc_update`, les brouillons de courriels, les écritures
  d'agenda, tout accès web, tout accès hors du dossier de travail ; dans le Bureau, chaque
  commande. opencode garde ces questions même après un « Toujours autoriser » pour les écritures
  dans Dokos, la messagerie et l'agenda.
- Identité : le shell de l'agent commite au nom de l'assistant (opencode : extension
  `dokopilot-guard` ; Claude Code : `env` de `.claude/settings.json`).
- Copies Git : `bin/dokopilot harness` ne lance aucun programme d'une copie (hooks et
  `core.fsmonitor` désactivés) et s'arrête si la configuration d'une copie contient un réglage
  inattendu (un agent aurait pu l'écrire pour qu'un programme tourne à votre place).
- Messagerie : le compte n'accorde que les brouillons (`allowed_mutations = ["draft"]`) et n'a
  aucun serveur d'envoi ; les brouillons attendent dans le dossier Brouillons.
- Claude Code : bac à sable du système (bubblewrap, Seatbelt sur macOS) pour les commandes, qui
  ne peuvent ni lire `~/.config/dokopilot` (donc ni le jeton Git ni la clé du modèle) ni écrire la
  configuration, les fichiers de consignes ou `.git/config` et `.git/hooks`, et ne joignent sans
  demander que Dokos (dirigeant) ; mode « bypass » désactivé.

**La garde « courriel ».** Une session qui a lu un courriel devient **exposée** : jusqu'à la fin
de cette session, toute action à effet (commande, fichier, même dans la boîte de réception, accès
web, écriture dans Dokos, brouillon, agenda) demande une confirmation, même dans l'Atelier. La
session est marquée *avant* que le courriel soit lu ; si la marque ne peut pas être enregistrée,
la lecture est bloquée. Le contenu d'un courriel est une donnée, jamais une instruction : les
consignes de l'agent et chaque confirmation le rappellent. Avec opencode, la marque est gardée
par l'extension (aussi après un redémarrage d'opencode 2) ; avec Claude Code, par un fichier par
session dans `~/.config/dokopilot/<client>/taint/`. Pour un travail sans lien avec les courriels,
ouvrez une nouvelle session.

## 6. Risques résiduels

- **Les jetons dans opencode.** opencode lit le jeton Dokos et la clé du modèle avec sa
  substitution `{file:…}`, et Git lit le jeton Forgejo avec son assistant d'identifiants : ils
  ne sont ni dans le dossier de travail ni dans l'environnement des commandes, mais opencode n'a
  pas de bac à sable. Dans l'Atelier, où les commandes passent sans accord, une commande
  détournée (écrite par un script, par exemple) peut encore lire `~/.config/dokopilot`, pousser
  avec le jeton de la personne ou modifier `.git/config` : les règles de refus reconnaissent les
  formes usuelles, pas toutes. Gardez l'Atelier pour du travail technique sans contenu extérieur,
  et préférez Claude Code avec son bac à sable pour ce travail.
- **Les commandes de l'Atelier écrivent partout dans le dossier de travail.** Les règles
  d'écriture (libre dans la boîte de réception et `perso/`, sur accord ailleurs) valent pour les
  outils de modification de fichiers ; une commande du shell de l'Atelier peut modifier n'importe
  quel fichier du dossier. Rien ne quitte le poste sans `bin/dokopilot sync` : relisez
  `git status` et `git diff` avant d'envoyer.
- **`bin/dokopilot sync` vérifie la copie, pas son contenu.** Il lance Git avec vos identifiants,
  hors de tout bac à sable, mais sans les crochets (hooks) ni le fsmonitor d'une copie, sans les
  variables `GIT_*` de la session, et avec vos seuls identifiants de Forgejo. Avant tout envoi, il
  refuse une copie dont la configuration Git a été modifiée (réglage hors de la liste attendue,
  autre assistant d'identifiants, adresse du serveur qui n'est pas celle de Forgejo), comme
  `bin/dokopilot harness`. Ce qu'il envoie, c'est ce que l'agent a commité : relisez-le.
- **Le jeton dans Claude Code.** Les identifiants sont lus par `headersHelper`, `apiKeyHelper` et
  l'assistant d'identifiants de Git, hors du bac à sable des commandes. Si bubblewrap et socat
  manquent, Claude Code lance les commandes sans bac à sable (sauf avec
  `harness_claude_sandbox_required: true`) et seules les règles de refus restent.
- **Ce que les jetons permettent.** Le jeton Dokos agit comme l'assistant (lire et préparer des
  brouillons, sans pouvoir valider) ; le jeton Git lit et écrit les dépôts de la personne. En cas
  de doute, l'intégrateur les régénère (voir [exploitation.md](exploitation.md)) puis relance
  `bin/dokopilot harness`.
- **Les procédures de l'entreprise et Claude Code.** Claude Code ne charge comme compétences que
  celles de `.claude/skills/` (donc celles de DokoPilot) ; celles de l'entreprise, dans
  `.agents/skills/`, lui sont accessibles par la règle 1 d'`AGENTS.md` (il lit leur `SKILL.md`),
  pas par l'outil Skill. opencode charge les deux.
- **Sans modèle configuré**, opencode choisit le modèle des titres lui-même : OpenCode 2 prend le
  petit modèle du fournisseur de la session, OpenCode 1 peut prendre un modèle hébergé par
  opencode. Renseignez `models.bureau` (ou `models.small`) pour l'éviter.
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
- **En mode réseau local** (démonstration), Forgejo et Dokos sont en HTTP : le jeton Git passe en
  clair sur le réseau local. La production passe par Tailscale (chiffré).
- **Tailscale** : les noms des machines figurent dans les journaux publics de certificats ;
  choisissez des noms neutres.
