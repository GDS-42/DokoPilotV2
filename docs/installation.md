# Installer DokoPilot

Ce guide décrit les deux façons d'installer DokoPilot : par le script `install.sh`, qui pose les
questions et lance tout (partie 1), ou à la main avec `bin/dokopilot` (partie 2). Le script ne
fait rien d'autre qu'écrire la configuration et appeler `bin/dokopilot` : les deux chemins
donnent la même installation. Pour un premier essai, suivez plutôt [vm-locale.md](vm-locale.md)
ou [vm-hyperv.md](vm-hyperv.md).

## Avant de commencer

### Les deux modes

| | Tout sur cette machine | Serveur distant |
| --- | --- | --- |
| Pour qui | Démonstration, très petite structure | Cas général : un serveur loué, piloté depuis un poste |
| Où lancer le script | Sur la machine qui sera le serveur | Sur votre poste |
| Système | Debian 13 **amd64** | Poste : Debian 13, Ubuntu 24.04 ou plus récent (WSL compris), Fedora 41 ou plus récent ; serveur : Debian 13 amd64 |
| Ressources | 8 Go de mémoire (au moins 7,5 Gio visibles), 30 Go libres dans `/var` (40 conseillés) | Poste : 2 Go de mémoire, 2 Go libres ; serveur : 4 vCPU, 8 Go, 40 Go (démonstration) ou 80 Go (production) |
| Compte de maintenance | Le vôtre : son mot de passe ne change pas, sudo vous le demande | Créé sur le serveur au premier passage ; son mot de passe sudo est dans les secrets chiffrés |
| Premier passage | Aucun | `bootstrap`, avec le compte initial du serveur (hébergeur ou installation de Debian) |
| Harnais d'agent | Sur cette même machine, pour votre compte | Sur votre poste |

Debian 12 n'est pas accepté comme poste : il fournit Python 3.11, et Ansible 2.21 demande
Python 3.12 ou plus. macOS n'est pas pris en charge par le script : suivez le chemin manuel
(partie 2).

### Ce que l'entrepreneur prépare, à son nom (production)

| Élément | Recommandation |
| --- | --- |
| Serveur | VPS Debian 13 **amd64**, 4 vCPU, 8 Go de RAM (4 Go au minimum, une partition d'échange est alors créée), 80 Go de disque, hébergé en France ou dans l'Union européenne, contrat et facturation au nom de l'entreprise |
| Réseau privé | Un réseau (« tailnet ») Tailscale dont l'entrepreneur est propriétaire ; l'intégrateur y est invité. Vérifiez sur tailscale.com/pricing que le plan choisi convient à un usage professionnel |
| Sauvegardes | Un compartiment S3 (ou un serveur SFTP ou REST restic) chez un autre fournisseur que le serveur, au nom de l'entreprise, avec une clé d'accès limitée à ce compartiment |
| Facturation électronique | Le contrat Super PDP et ses identifiants (eTransactions) |
| Modèle d'IA | Un compte chez le fournisseur de modèle choisi, avec un plafond de dépenses ; sa clé reste sur le poste de chaque personne, jamais sur le serveur |

Ce découpage garde l'entrepreneur maître de ses données et libre de changer d'intégrateur.

### Régler Tailscale (production)

Dans la console d'administration du tailnet :

1. **DNS** : activez MagicDNS puis « Enable HTTPS ». Les noms de machines apparaissent dans les
   journaux publics de certificats : choisissez un nom neutre (`network.tailscale.hostname`, par
   défaut `dokos-<client>`).
2. **Politique d'accès** : déclarez une étiquette pour le serveur et n'ouvrez que les ports 443
   (Dokos) et 8443 (Forgejo), par exemple :

   ```jsonc
   {
     "tagOwners": { "tag:dokopilot": ["autogroup:admin"] },
     "grants": [
       { "src": ["autogroup:member"], "dst": ["tag:dokopilot"], "ip": ["tcp:443", "tcp:8443"] }
     ],
     "tests": [
       {
         "src": "dirigeant@exemple.fr",
         "accept": ["tag:dokopilot:443", "tag:dokopilot:8443"],
         "deny": ["tag:dokopilot:22"]
       }
     ]
   }
   ```

   Retirez l'attribut `funnel` des `nodeAttrs` s'il est présent : Funnel publierait un service sur
   Internet. Si la console le demande, autorisez Serve pour ce serveur : Dokos et Forgejo sont
   publiés par `tailscale serve`.
3. **Appareils** : exigez l'approbation manuelle des nouveaux appareils.
4. **Clé d'authentification** : créez une clé à usage unique, pré-approuvée, avec l'étiquette
   `tag:dokopilot`, valable quelques jours (Settings › Keys › Generate auth key). Elle ne sert
   qu'au premier passage.

En mode Tailscale, le poste qui lance l'installation doit lui aussi être connecté au tailnet :
`bin/dokopilot harness` copie les dépôts de contexte depuis Forgejo, joignable seulement par ce
réseau.

## Partie 1 — Installation par le script

### Lancer le script

Avec votre compte personnel (pas root ; il doit pouvoir utiliser sudo) :

```bash
curl -fsSL https://raw.githubusercontent.com/GDS-42/DokoPilotV2/v0.2.0/install.sh | sh
```

ou, pour lire le script avant de le lancer :

```bash
curl -fsSLO https://raw.githubusercontent.com/GDS-42/DokoPilotV2/v0.2.0/install.sh
less install.sh
sh install.sh            # sh install.sh --check : vérifier seulement
```

| Option | Effet |
| --- | --- |
| `--check` | Vérifications seules, sans rien installer ; code de sortie 1 si la machine ne convient pas |
| `--answers <fichier>` | Répond aux questions avec ce fichier, sans rien demander ([modèle commenté](../installer/answers.example.yml)) |
| `--yes` | Répond oui aux confirmations dont la réponse proposée est oui (jamais au remplacement d'une installation existante) |
| `--version <étiquette>` | Version de DokoPilot à installer (défaut : `v0.2.0`) |
| `--dir <dossier>` | Dossier de DokoPilot sur cette machine (défaut : `~/DokoPilotV2`) |

Le script fait cinq choses, dans cet ordre :

1. **Vérifie la machine** : système (`/etc/os-release`), architecture, mémoire, espace libre,
   sudo, accès aux sites nécessaires (GitHub, PyPI, Ansible Galaxy ; pour un serveur sur cette
   machine, aussi les dépôts Debian et Docker, les registres de GitLab, Docker Hub et Codeberg,
   npm). Il dit aussi si le mode « tout sur cette machine » est possible ici, et pourquoi sinon.
   Un point marqué ✗ arrête l'installation.
2. **Installe les paquets manquants** avec `apt` ou `dnf`, par sudo, après vous l'avoir demandé :
   `git`, `python3` et `python3-venv`, `age`, client OpenSSH, `make`, `curl`.
3. **Récupère DokoPilot** à la version demandée (`git clone --depth 1 --branch v0.2.0`) dans
   `~/DokoPilotV2`. Une copie déjà là est passée à cette version, sauf si vous y avez modifié des
   fichiers.
4. **Prépare ses outils** : `make setup` (Ansible et ses collections dans `~/DokoPilotV2/.venv`).
5. **Lance l'assistant** `installer/wizard.py`, qui pose les questions ci-dessous puis installe.

Ni le script ni l'assistant ne lancent Ansible eux-mêmes ou ne gardent un mot de passe sudo :
l'assistant appelle `bin/dokopilot`, et les mots de passe sudo sont tapés quand Ansible les
demande.

### Les questions de l'assistant

L'assistant compte six étapes ; les cinq premières posent les questions. Entre crochets, la
réponse proposée (Entrée pour l'accepter). Les secrets se tapent sans écho.

**Étape 1 — Où installer**

| Question | Réponses |
| --- | --- |
| Où installer DokoPilot ? | `local` (tout sur cette machine) ou `distant` [distant] |

- **Local** : l'assistant vérifie de nouveau la machine (Debian 13, amd64, mémoire, `/var`, sudo,
  compte nominatif lancé sans root, sites du serveur joignables) et refuse ce mode si un point
  manque. Votre compte devient le compte de maintenance ; il doit avoir un mot de passe
  utilisable, que sudo demandera. Un compte créé par cloud-init n'en a souvent pas (celui de
  `vm/make-seed.sh` n'en a pas) : donnez-lui-en un avec `sudo passwd <compte>` avant de
  commencer, sinon `install` s'arrête. Les clés de `~/.ssh/authorized_keys` restent autorisées
  (sans leurs options `from=`, `command=`, qui sont retirées) et seront les seules à ouvrir une
  session SSH à distance. S'il n'y en a pas, l'assistant demande la clé publique de l'ordinateur
  qui administrera la machine (texte `ssh-ed25519 …` ou fichier `.pub`) ; sans réponse, il crée
  une clé propre à cette machine (`~/.ssh/id_ed25519`), car le compte doit en avoir une.
- **Distant** : adresse du serveur (IP ou nom) ; port SSH [22] ; compte initial du serveur, donné
  par l'hébergeur ou créé à l'installation de Debian, qui ne sert qu'une fois [debian] ; votre
  compte de maintenance, nominatif, créé sur le serveur [votre compte] ; votre clé SSH publique
  [~/.ssh/id_ed25519.pub], que l'assistant propose de créer si elle manque.

**Étape 2 — Démo ou production, agent**

| Question | Réponses |
| --- | --- |
| Démo ou production ? | `demo` (l'Atelier Démo et ses données de test) ou `production` [demo] |
| Identifiant court de cette installation | Minuscules, chiffres, tirets : dossier `clients/<id>`, organisation Forgejo [atelier-demo en démo]. `demo` est refusé : c'est l'exemple versionné du dépôt |
| Quel agent utiliser sur ce poste ? | `opencode`, `claude-code` ou `les-deux` [opencode] |

**Étape 3 — Modèle d'IA**

| Question | Réponses |
| --- | --- |
| Fournisseur du modèle (avec opencode) | `anthropic`, `mistral`, `openai`, `openrouter`, `vercel`, `scaleway`, `ollama` ou `autre` (identifiant opencode à indiquer) [anthropic] ; avec Claude Code seul : Anthropic |
| Modèle Bureau, modèle Atelier, petit modèle | Proposés pour Anthropic (`claude-sonnet-5-5`, `claude-opus-5-5`, `claude-haiku-5-5`) et Mistral (`mistral-large-4`, petit modèle = Bureau) ; pour les autres, à indiquer, ou Entrée pour choisir plus tard dans l'agent ([D-31](decisions.md)) |
| Claude Code : abonnement ou clé d'API ? | `abonnement` (connexion par `/login`) ou `cle-api` [abonnement] ; avec opencode et un autre fournisseur qu'Anthropic, Claude Code utilise l'abonnement |
| Clé d'API du fournisseur | Saisie masquée, quand elle est nécessaire (pas pour Ollama) ; écrite dans `~/.config/dokopilot/<id>/model-api-key` (0600), jamais dans `clients/` ni dans les secrets du serveur |

**Étape 4 — Entreprise et personnes**

| Question | Réponses |
| --- | --- |
| Raison sociale | Telle qu'elle apparaîtra dans Dokos [Atelier Démo en démo] |
| Abréviation dans Dokos | 2 à 5 lettres ou chiffres [tirée de la raison sociale] |
| SIREN | 9 chiffres, clé de contrôle vérifiée (production seulement) |
| Le dirigeant : prénom, nom, courriel, identifiant, un assistant ? | L'identifiant nomme ses comptes et son dépôt `perso-<id>` [tiré du prénom] ; l'assistant [oui] est l'identité de son agent dans Dokos et dans Forgejo |
| Ajouter une autre personne ? | [non] ; chaque membre a les mêmes questions ; son assistant n'existe que dans Forgejo en version 0.2 ([D-29](decisions.md)) |

**Étape 5 — Réseau, messagerie, agenda, import**

| Question | Réponses |
| --- | --- |
| Comment joindre Dokos et Forgejo ? | En démo : `lan` (réseau local, HTTP) ou `tailscale` [lan] ; en production, toujours Tailscale |
| Clé d'authentification Tailscale | Avec Tailscale : `tskey-auth-…`, saisie masquée, rangée dans les secrets chiffrés |
| Dépôt de sauvegarde restic | Production : dépôt distant (`s3:…`, `sftp:…`, `rest:…`) ; pour S3, identifiant et clé secrète, saisis masqués et rangés dans les secrets ; sinon, à ajouter ensuite (`restic_env`) |
| Brancher une messagerie ? | [non] ; sinon serveur IMAP, port [993], chiffrement, identifiant, adresse affichée, mot de passe (masqué, écrit dans `~/.config/dokopilot/<id>/mail-password`) |
| Brancher un agenda CalDAV ? | [non] ; sinon adresse `https://…`, identifiant, mot de passe (masqué, `~/.config/dokopilot/<id>/caldav-password`) |
| Dossier à importer dans la boîte de réception | Un dossier de ce poste, par exemple un coffre Obsidian [aucun] |

### Ce qui se passe ensuite (étape 6)

L'assistant affiche un récapitulatif et demande confirmation, puis enchaîne les actions
ci-dessous. Chacune est notée faite dès qu'elle réussit.

| Action | Effet |
| --- | --- |
| Écriture des fichiers | `clients/<id>/client.yml` (réglages, sans secret) et `client.local.yml` (serveur, compte de maintenance) ; un fichier déjà présent est d'abord copié en `<fichier>.<date>.bak` |
| `bin/dokopilot init <id> --profile …` | Clé age (`~/.config/dokopilot/age-identity.txt`, créée si elle manque) et secrets chiffrés `clients/<id>/secrets.yml.age`, mots de passe tirés au hasard |
| Rangement des secrets | Clé Tailscale et identifiants S3 ajoutés aux secrets chiffrés, par `bin/dokopilot secrets <id> edit` |
| `bin/dokopilot config <id>` | Validation de la configuration |
| `bin/dokopilot bootstrap <id> -K` | Serveur distant seulement : l'assistant vérifie d'abord que le compte initial accepte votre clé (sinon il propose `ssh-copy-id`, qui demande son mot de passe), puis crée votre compte de maintenance et ferme SSH à tout autre compte |
| `bin/dokopilot install <id>` | Tout le serveur : durcissement, Docker, Dokos, Forgejo et le contexte, publication, sauvegardes. De 30 à 60 minutes ; se relance sans risque |
| `bin/dokopilot harness <id>` | Le harnais du dirigeant sur ce poste : copies des dépôts dans `~/DokoPilot/<id>`, jetons dans `~/.config/dokopilot/<id>/` ([poste-entrepreneur.md](poste-entrepreneur.md)) |
| `bin/dokopilot check <id>` | Contrôles de fonctionnement et de sécurité |

En mode « tout sur cette machine », `install`, `harness` et `check` sont lancés avec
`--local -K`.

À la fin, l'assistant affiche un résumé : adresses de Dokos et de Forgejo ; compte du dirigeant
dans Dokos (mot de passe initial : `bin/dokopilot secrets <id> show entrepreneur_password`) et
dans Forgejo (compte `<id de la personne>`, mot de passe initial dans
`~/.config/dokopilot/<id>/forgejo-password`, à changer) ; l'assistant du dirigeant ; les membres ;
l'espace de travail ; les prochaines étapes (sauvegarder la clé age, lancer l'agent, envoyer son
travail avec `bin/dokopilot sync`) ; enfin les programmes encore à installer sur le poste
(opencode, Claude Code, bubblewrap et socat, uv, Node.js selon vos choix). Les commandes
`bin/dokopilot` se lancent depuis `~/DokoPilotV2`.

**Sauvegardez `~/.config/dokopilot/age-identity.txt`** dans votre gestionnaire de mots de passe :
sans elle, les secrets du client sont illisibles.

### Le mot de passe sudo (« BECOME password »)

Ansible demande les mots de passe sudo par l'invite `BECOME password:` ; rien n'est enregistré.

- **Tout sur cette machine** : à chaque `install`, `harness` et `check`, tapez le mot de passe de
  votre propre compte. Il n'est jamais modifié.
- **Serveur distant** : seul `bootstrap` le demande, pour le compte initial du serveur ; si ce
  compte n'a pas de mot de passe (root, ou sudo sans mot de passe d'une image cloud), appuyez sur
  Entrée. Ensuite, `bin/dokopilot` fournit lui-même le mot de passe sudo du compte de
  maintenance, tiré des secrets (`bin/dokopilot secrets <id> show maintenance_sudo_password`).

### Reprendre une installation interrompue

Les réponses et les étapes faites sont gardées dans `clients/<id>/.assistant-etat.json` (ignoré
par Git, 0600, sans aucun secret) dès que l'identifiant est connu (étape 2). Si une étape échoue,
l'assistant dit laquelle et pourquoi ; corrigez la cause, puis relancez au choix :

```bash
sh install.sh                                          # ou la commande curl de départ
~/DokoPilotV2/.venv/bin/python ~/DokoPilotV2/installer/wizard.py
```

L'assistant retrouve l'installation commencée, demande s'il faut la reprendre, passe les étapes
déjà faites et reprend à la première qui reste. Les secrets saisis (clé Tailscale, identifiants
S3) ne sont pas gardés dans le fichier d'état : s'ils n'avaient pas encore été rangés, ils sont
redemandés. Pour tout recommencer, répondez non à la reprise, puis confirmez le remplacement
quand l'assistant le demande. Une installation terminée (contrôles passés) n'est plus proposée à
la reprise.

### Installer sans question (`--answers`)

```bash
sh install.sh --answers mes-reponses.yml
```

Partez de [`installer/answers.example.yml`](../installer/answers.example.yml) : les clés sont en
anglais, les valeurs s'écrivent comme à la question ; une clé absente prend la valeur proposée,
et l'assistant s'arrête en nommant une réponse manquante sans valeur proposée. Un secret ne
s'écrit jamais dans ce fichier : donnez `<nom>_env` (une variable d'environnement) ou
`<nom>_file` (un fichier). `resume: non` recommence une installation commencée ;
`stop_after: config` (ou `install`, `harness`) s'arrête après cette étape. Les mots de passe sudo
restent demandés par Ansible : sans terminal, arrêtez-vous à la configuration (`stop_after:
config`) et installez ensuite à la main (partie 2).

## Partie 2 — Le chemin manuel

Les mêmes étapes, commande par commande, depuis une copie de DokoPilot :

```bash
git clone --branch v0.2.0 https://github.com/GDS-42/DokoPilotV2.git ~/DokoPilotV2
cd ~/DokoPilotV2
make setup                   # Ansible et ses collections dans .venv ; puis bin/dokopilot doctor
```

Il faut Python 3.12 ou plus avec `venv`, `make`, `age`, `git`, `ssh` et une clé SSH
(`ssh-keygen -t ed25519`).

### 1. Préparer la configuration

```bash
bin/dokopilot init acme --profile production \
  --address <adresse du serveur> --bootstrap-user <compte initial> \
  --maintenance-user <votre compte> --ssh-key ~/.ssh/id_ed25519.pub \
  --recipient age1…entrepreneur… --recipient age1…secours…
```

- `init` crée `clients/acme/client.yml` à partir de
  [`config/client.example.yml`](../config/client.example.yml), `client.local.yml` (adresse,
  comptes), votre clé age et les secrets chiffrés. `--recipient` ajoute la clé age publique de
  l'entrepreneur (`age-keygen` sur son poste) et une clé de secours imprimée et rangée en lieu
  sûr : chacune peut déchiffrer les secrets. Relancé, `init` complète sans écraser.
- Complétez `clients/acme/client.yml` en suivant ses commentaires :
  - `client` : raison sociale, abréviation, SIREN (obligatoire en production) ;
  - `maintenance.users` : les comptes de maintenance et leurs clés (remplacez l'exemple
    `integrateur`) ;
  - `users.people` : chaque personne (`id`, `email`, `first_name`, `last_name`, `role`,
    `assistant`), avec **exactement un** `role: dirigeant` ; l'adresse d'un assistant vaut par
    défaut `assistant.<id>@<domaine de email>`. L'ancien format (`users.entrepreneur`,
    `users.assistants`) est refusé, avec la nouvelle forme à recopier ;
  - `context` : `enabled` (Forgejo et le contexte, par défaut `true`), `direction` (le dépôt
    `direction`), `import_path` (dossier du poste à importer une fois) ;
  - `models` : `provider`, `bureau`, `atelier`, `small` (`<fournisseur>/<modèle>`, ou vides pour
    choisir dans l'agent ; un `small` vide prend le modèle `bureau`), `claude_code_auth`
    (`subscription` ou `api_key`). La clé du fournisseur ne va ni ici ni dans les secrets : elle
    reste sur le poste (`~/.config/dokopilot/<client>/model-api-key`, demandée par
    `bin/dokopilot harness` si elle manque) ;
  - production : `network.tailscale.advertise_tags: ["tag:dokopilot"]`, `backup.repository`.
- Complétez les secrets : `bin/dokopilot secrets acme edit` (`tailscale_auth_key`, `restic_env`
  avec les clés S3, `etransactions` avec les identifiants Super PDP). `forgejo_admin_password`
  est tiré au hasard par `init`.
- Vérifiez : `bin/dokopilot config acme`. La validation refuse une configuration de production
  sans Tailscale, avec des données de test ou avec une sauvegarde locale.
- Rangez `clients/acme/` dans un **dépôt Git privé** propre au client (il contient des données
  personnelles) ; `DOKOPILOT_CLIENTS_DIR` indique où il se trouve.

**Tout sur cette machine.** Sur la machine Debian 13 elle-même, déclarez dans
`clients/<client>/client.local.yml` :

```yaml
server:
  local: true
  address: localhost
  hostname: <nom actuel de la machine>   # sinon la machine est renommée dokopilot-<client>
maintenance:
  users:
    - name: <votre compte>
      full_name: <Prénom Nom>
      ssh_public_keys:
        - "ssh-ed25519 AAAA… ordinateur-d-administration"
```

Les clés listées remplacent le contenu de `~/.ssh/authorized_keys` de ce compte : mettez-y toutes
celles qui doivent rester valables.

### 2. Installer

Serveur distant :

```bash
bin/dokopilot bootstrap acme -K      # avec le compte initial (server.bootstrap_user)
bin/dokopilot install acme           # avec votre compte de maintenance
bin/dokopilot check acme
```

`bootstrap` se connecte avec `server.bootstrap_user` (ou le compte de `--user`) ; à l'invite
`BECOME password`, tapez son mot de passe sudo, ou Entrée s'il n'en a pas (root). Le premier
passage crée les comptes de maintenance et ferme SSH à tout autre compte : gardez
ouverte la console de secours de l'hébergeur jusqu'à ce que `bin/dokopilot ssh acme` fonctionne.
L'installation complète prend de 30 à 60 minutes ; elle se relance sans risque. Le serveur prend
le nom `server.hostname` (par défaut `dokopilot-<client>`).

Tout sur cette machine (pas de `bootstrap`) :

```bash
bin/dokopilot install <client> --local -K
bin/dokopilot check <client> --local -K
```

`-K` fait demander par Ansible le mot de passe sudo de votre compte. `server.local: true` vaut
`--local` pour toutes les commandes ; `-K` reste nécessaire.

À la fin, Dokos est joignable à `https://dokos-<client>.<tailnet>.ts.net` et Forgejo à
`https://dokos-<client>.<tailnet>.ts.net:8443/` depuis les appareils du tailnet (production), ou
à `http://<adresse>` et `http://<adresse>:3000/` depuis le réseau local (`network.mode: lan`) ;
`http://localhost` et `http://localhost:3000/` sur la machine elle-même en mode local.

### 3. Le poste de chaque personne

```bash
bin/dokopilot harness acme                    # le dirigeant, dans ~/DokoPilot/acme
bin/dokopilot harness acme --person lucas     # un membre (id de users.people)
bin/dokopilot sync acme                       # envoyer le travail enregistré sur ce poste
```

En mode local, ajoutez `--local -K` à `harness`. Détails, prérequis et garde-fous :
[poste-entrepreneur.md](poste-entrepreneur.md).

## Remettre les accès

- **Dokos** : l'entrepreneur se connecte avec son adresse et le mot de passe initial
  (`bin/dokopilot secrets acme show entrepreneur_password`), puis le change.
- **Forgejo** : chaque personne se connecte avec son identifiant et le mot de passe initial de
  `~/.config/dokopilot/<client>/forgejo-password`, puis le change dans Forgejo (Configuration ›
  Compte). Rien ne l'y oblige : une obligation bloquerait Git ([D-30](decisions.md)).
- **Poste** : installez Tailscale, l'agent et le harnais sur l'ordinateur de chaque personne :
  [poste-entrepreneur.md](poste-entrepreneur.md).
- **Sauvegarde** : `bin/dokopilot backup acme`, vérifiez la présence de l'instantané (sur le
  serveur : `sudo bash -c 'set -a; . /etc/dokopilot/restic.env; set +a; restic snapshots'`),
  puis faites un essai de restauration sur une VM de test ([exploitation.md](exploitation.md)).
- **Clé age** : si l'installation a été faite par le script, ajoutez les clés de l'entrepreneur et
  de secours : `bin/dokopilot init acme --recipient age1…` (les secrets sont rechiffrés).
- **RGPD** : l'entrepreneur est responsable de traitement ; l'intégrateur, sous-traitant au sens
  de l'article 28 du RGPD, signe un contrat de sous-traitance qui décrit ses accès de maintenance.

## Ensuite

Mises à jour, supervision, restauration, personnes à ajouter ou à retirer dans Forgejo :
[exploitation.md](exploitation.md). Pour retirer un intervenant, supprimez son compte de
`maintenance.users` et relancez `bin/dokopilot install acme` : son compte est verrouillé et ses
clés retirées.
