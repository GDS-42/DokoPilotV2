# Registre des décisions

Chaque décision indique le contexte, le choix, les options écartées et ce qui la ferait revoir. Les décisions de produit (architecture simplifiée, harnais au choix, auto-hébergement, Tailscale) ont été prises avec le porteur du projet le 2026-11-03 et sont reprises ici ; les décisions techniques ont été tranchées pendant la construction du dépôt, le 2026-11-04, et restent à confirmer par le porteur du projet.

## D-01 — S'appuyer sur les briques existantes

- **Contexte** : la première version (dépôt DokoPilot) prévoyait de développer un harnais, un cockpit et des outils métier. Dokos AI fournit déjà un serveur MCP avec permissions, journal d'audit et validations ; opencode et Claude Code sont des harnais matures.
- **Choix** : ne rien développer côté métier ; ce dépôt se limite à l'infrastructure as code (serveur, Dokos et ses applications, provisionnement, sauvegardes) et à la configuration des harnais sur le poste.
- **Revoir si** : un besoin métier essentiel n'est couvert ni par Dokos, ni par Dokos AI, ni par un connecteur existant.

## D-02 — Ansible et Docker Compose, une commande unique

- **Choix** : Ansible (rôles idempotents) pour le serveur, Docker Compose pour la pile Dokos, un CLI `bin/dokopilot` qui assemble configuration, secrets et inventaire. Un seul fichier `client.yml` par client (complété par `client.local.yml`, fusionné en profondeur par le CLI), fusionné avec les valeurs par défaut du profil dans le fait `dp`, validé avant toute modification. Le CLI refuse tout réglage de premier niveau inconnu : les variables internes des rôles ne se règlent pas depuis un fichier client.
- **Version d'Ansible** : ansible-core 2.21, qui fonctionne avec Python 3.12 et plus (Ubuntu 24.04, WSL, Debian 13) ; la 2.22 exige Python 3.13.
- **Écartés** : Kubernetes (surdimensionné pour un serveur unique), scripts shell seuls (ni idempotents ni testables), Terraform (le VPS est loué par l'entrepreneur, souvent sans API).
- **Revoir si** : plusieurs serveurs par client deviennent la norme.

## D-03 — Debian 13, serveur amd64

- **Choix** : Debian 13 (trixie), seule distribution prise en charge. Le serveur doit être **amd64** (x86-64) : l'image officielle de Dokos n'existe qu'en amd64 (`registry.gitlab.com/dokos/dokos`, vérifié sur v5.18.11). Les rôles système fonctionnent aussi en arm64, mais pas la pile Dokos.
- **Raison** : support long, paquets libres, disponible chez tous les hébergeurs français ; une seule cible à tester.
- **Conséquence** : sur un Mac Apple Silicon, la VM de test doit émuler l'amd64 (UTM en mode émulation, lent) ou tourner sur une autre machine.
- **Revoir si** : Dokos publie une image arm64, ou si nous construisons l'image complète depuis l'image Dodock, qui est multi-architecture.

## D-04 — Secrets chiffrés avec age, plusieurs destinataires

- **Choix** : `clients/<client>/secrets.yml.age`, chiffré avec age pour les clés publiques de `recipients.txt` (intégrateur, entrepreneur, clé de secours). Le CLI déchiffre dans un dossier temporaire privé le temps d'une exécution Ansible, puis l'efface. Sur le serveur, les secrets vivent dans des fichiers lisibles par root seulement.
- **Écartés** : SOPS (outil supplémentaire à installer sur chaque poste, sans gain réel pour un fichier par client), Ansible Vault (mot de passe partagé, pas de destinataires à clé publique, réversibilité moins simple pour l'entrepreneur).
- **Conséquence** : le fichier est chiffré en entier ; un diff Git ne montre pas quelle clé a changé.
- **Revoir si** : plusieurs personnes modifient souvent les secrets d'un même client.

## D-05 — Exposition : Tailscale en production, réseau local en démo

- **Choix** : en production, Dokos écoute sur 127.0.0.1 et n'est publié que par `tailscale serve` (HTTPS sur le nom MagicDNS). Seul le port SSH de maintenance est ouvert sur Internet. Le mode `lan` (HTTP par Caddy, filtré sur des plages privées) n'existe que pour le profil demo.
- **Écartés** : exposition publique derrière un proxy (surface d'attaque), WireGuard brut (gestion des clés et des pairs à la main).
- **Pare-feu** : aucun port UDP n'est ouvert pour Tailscale (il établit ses connexions directes depuis le serveur, sinon il passe par ses relais) ; sur l'interface Tailscale, seuls HTTPS (Dokos) et SSH sont acceptés, les règles d'accès du tailnet filtrant en plus qui peut les joindre.
- **Conséquences** : l'entrepreneur et ses postes rejoignent le réseau Tailscale ; un harnais en nuage (application Claude) ne peut pas joindre Dokos, d'où Claude Code sur le poste ; eTransactions et bank ne font que des appels sortants, compatibles.

## D-06 — Accès de maintenance

- **Choix** : comptes nominatifs, clé SSH uniquement, `sudo` avec mot de passe (stocké dans les secrets ; le CLI le fournit à Ansible), pas de connexion root, fail2ban sur SSH, groupe `dokopilot-maint`. Les comptes retirés de la configuration sont verrouillés, pas supprimés. Le compte initial de l'hébergeur ou de l'installation (`server.bootstrap_user`, sauf root) sert au premier passage, puis perd son mot de passe, ses groupes et ses clés ; root ne se connecte jamais en SSH.
- **Raison** : une clé SSH volée, ou un agent qui pilote l'installation, ne donne pas à elle seule les droits root.

## D-07 — Docker CE du dépôt officiel, pare-feu nftables dans sa propre table

- **Choix** : Docker CE (dépôt download.docker.com, protections réseau des versions 28 et suivantes), conteneurs publiés sur 127.0.0.1 uniquement ; nftables avec une table `inet dokopilot` à politique « drop » en entrée, sans jamais vider les tables de Docker et de Tailscale.
- **Écarté** : `docker.io` de Debian (version plus ancienne, sans ces protections), ufw (cohabite mal avec Docker).

## D-08 — Sauvegardes restic

- **Choix** : sauvegarde quotidienne (base et fichiers par `bench backup --with-files`) vers un dépôt restic chiffré ; en production le dépôt est obligatoirement distant (S3, SFTP ou serveur REST) ; rétention 7 jours, 4 semaines, 12 mois ; restauration outillée et testée.

## D-09 — VM locale par cloud-init, sans Vagrant

- **Choix** : image officielle Debian 13 « generic » et image d'amorçage cloud-init (NoCloud) générée par `vm/make-seed.sh`, utilisable avec tout hyperviseur ; script automatique pour libvirt.
- **Écarté** : Vagrant (licence BSL, non libre).

## D-10 — Pas de caisse

- **Choix** : ni le profil demo ni le profil production n'installent `certification_caisse` ou le point de vente. Le périmètre certifié reste hors du champ de ce dépôt.
- **Revoir si** : un client a besoin d'une caisse certifiée ; il faudra alors suivre la plage de versions déclarée par Dokos pour la certification, ce que ne fait pas la version courante de l'image.

## D-11 — Pas d'application bank : relevés importés

- **Contexte** : l'application `bank` (v2.6.3) ne parle qu'à l'agrégateur Bridge, par un relais exploité par Dokos qui exige des identifiants délivrés par Dokos ; la documentation précise que la synchronisation bancaire est « uniquement disponible sur Dokos Cloud ».
- **Choix** : ne pas installer `bank`. Les transactions bancaires entrent dans Dokos par l'import de relevés du cœur de Dokos (OFX, CSV, XLSX, MT940), fait par l'entrepreneur ; l'agent propose ensuite le rapprochement. La banque reste ainsi en lecture seule : aucun agent n'a d'accès à la banque.
- **Revoir si** : Dokos ouvre son relais aux installations auto-hébergées, ou un client souscrit un agrégateur pris en charge.

## D-12 — Image Dokos dérivée de l'image officielle

- **Choix** : `FROM registry.gitlab.com/dokos/dokos:v5.18.11` figée par empreinte (sha256), à laquelle on ajoute seulement Dokos AI v1.14.0 (`bench get-app`) ; eTransactions est déjà dans l'image officielle. L'image est construite sur le serveur, uniquement quand ses entrées changent.
- **Écarté** : reconstruire toute l'image avec frappe_docker (plus long, plus de mémoire, et nous ne livrerions plus la combinaison testée par Dokos).
- **Point accepté** : Dokos AI fige `litellm==1.83.7`, qui impose `click==8.1.8` alors que Dodock demande `Click~=8.3.1`. L'intégration continue de Dokos AI installe l'application de la même manière et vit avec ce retour en arrière ; nous faisons de même, sans modifier aucun fichier amont, et vérifions les commandes `bench` après la construction.
- **Revoir si** : Dokos AI relâche cette contrainte, ou un défaut lié à click apparaît.

## D-13 — MariaDB 11.8 et Valkey

- **Choix** : MariaDB 11.8 (version LTS ; la 10.6 documentée par Docli n'est plus maintenue depuis juillet 2026) et Valkey 8 pour le cache et la file de tâches (licence BSD, compatible Redis ; Redis 7.4 et suivants ne sont plus sous licence libre). Versions figées par empreinte.
- **Revoir si** : Dokos annonce une incompatibilité avec l'une de ces versions.

## D-14 — Un rôle « Assistant IA » sans validation

- **Contexte** : les permissions Frappe s'additionnent entre rôles, et le serveur MCP de Dokos AI expose `doc_submit`, `doc_cancel` et `doc_delete` dès que les écritures sont permises, sans réglage par outil.
- **Choix** : chaque assistant n'a que deux rôles, `Assistant IA` (créé par l'installation : lecture, création et modification de brouillons, jamais valider, annuler, amender ni supprimer) et `AI Writer` ; aucun rôle métier standard. Les harnais refusent en plus ces trois outils côté poste. Les permissions sont créées par le gestionnaire de permissions de Frappe, jamais par une ligne isolée de permission personnalisée (qui retirerait l'accès des rôles standard).
- **Conséquence** : le pipeline « Suggest Bank Transaction Reconciliation » de Dokos AI, dont la dernière étape valide un paiement, ne peut pas tourner sous l'identité d'un assistant ; il reste manuel, lancé par une personne.
- **Revoir si** : Dokos AI permet de désactiver des outils MCP un par un ; proposer cette évolution en amont.

## D-15 — Règles de provisionnement de Dokos

Tranchées pendant la construction de la pile, vérifiées par `tests/stack/run.sh` :

- **Assistants** : pas de permission « e-mail » (l'envoi depuis Dokos serait un effet externe) ; tout rôle en trop trouvé sur un assistant est retiré à chaque passage ; un assistant retiré de `client.yml` est désactivé et son jeton supprimé ; le jeton n'est créé que s'il n'existe pas.
- **Mots de passe** : celui d'`Administrator` est réappliqué à chaque passage (les secrets font foi) ; celui de l'entrepreneur seulement à la création, puisqu'il le change ensuite.
- **eTransactions** : la réception reste active et `auto_submit_purchase_invoices` reste à 0 à chaque passage ; avec des identifiants Super PDP, la plateforme d'achat est créée.
- **Création du site** : par le code de `bench new-site` appelé en Python, pour que les mots de passe n'apparaissent jamais sur une ligne de commande ; `bench migrate` ne tourne que lorsque l'image change.
- **Démo** : `demo-reset` supprime et recrée le site, garde les jetons des assistants et refuse un site sans le marqueur de démo ; une restauration remet aussi la clé de chiffrement du site sauvegardé.
- **Points amont relevés** (sans modification de notre part) : eTransactions 2.17.0 ne rapproche pas les articles par l'identifiant acheteur des factures CII et ne reporte pas le numéro de commande sur la facture fournisseur ; les brouillons ont un nom provisoire jusqu'à leur validation ; un journal « Banque » est nécessaire aux paiements, créé par l'assistant de configuration seulement si un compte bancaire lui est donné (voir exploitation.md). Ces points sont à signaler à Dokos.

## D-16 — Composants non libres acceptés, et leurs alternatives

La règle « logiciels libres uniquement » s'applique à ce dépôt et à ce qu'il installe. Deux services ou outils non libres sont acceptés, parce qu'ils sont facultatifs ou remplaçables sans changer l'architecture :

- **Claude Code** (harnais propriétaire d'Anthropic) : option sur le poste de l'entrepreneur ; le harnais par défaut est opencode (MIT).
- **Service de coordination de Tailscale** (service en ligne propriétaire ; le client et le démon sont sous licence BSD-3) : il ne voit que des métadonnées de connexion, jamais le trafic, chiffré de bout en bout par WireGuard. Alternative libre : Headscale (BSD-3), serveur de coordination auto-hébergé, compatible avec le client Tailscale ; à prévoir si un client refuse ce service.
- **Revoir si** : un client exige une chaîne entièrement libre (Headscale et opencode seuls).

## D-17 — Pas de fonctionnalité payante de GitHub

- **Contexte** : le dépôt est privé ; GitHub Actions y consomme des minutes facturées au-delà du quota gratuit, et la première exécution a été refusée pour une raison de facturation du compte.
- **Choix** : aucun flux GitHub Actions, ni aucune autre fonctionnalité payante (Codespaces, Packages, LFS). Les vérifications tournent sur le poste ou dans la session de travail : `make ci` (lint, tests rapides, rôles système), plus `make test-e2e` et `make test-stack` quand l'installation ou la pile Dokos changent ; chaque pull request indique les commandes lancées et leurs résultats.
- **Revoir si** : le dépôt devient public (Actions y est gratuit) ou le porteur du projet choisit un service d'intégration continue.


## D-18 à D-28 — Contexte préinstallé et installation en une commande (version 0.2)

Décisions prises avec le porteur du projet le 2026-11-06 ; la spécification qui en découle est [plan-v0.2.md](plan-v0.2.md). Elles prolongent le dossier de transmission du système IA (organisation du contexte en trois couches : statique, capacités, dynamique).

| # | Décision | Raison |
| --- | --- | --- |
| D-18 | La référence du contexte est un ensemble de dépôts Git dans **Forgejo**, sur le serveur de l'entreprise, joignable seulement par le réseau privé | Source unique dans le réseau privé, droits par équipe, interface de relecture ; Forgejo est libre (GPL-3.0 ou plus) |
| D-19 | Le script s'utilise en **deux modes** : tout sur cette machine (Debian 13) ou piloter un serveur distant depuis un poste (Debian, Ubuntu, Fedora) | Démo, très petite structure et cas général avec le même outil |
| D-20 | Trois sortes de dépôts : **entreprise** (tous), **direction** (dirigeant et son assistant), **perso** (un par personne) | Git ne gère pas la lecture fichier par fichier : un dépôt par périmètre de droits |
| D-21 | L'agent écrit **librement** dans la boîte de réception et dans le contexte perso, **sur accord** ailleurs ; il ne pousse jamais : la personne pousse (`bin/dokopilot sync`) | L'agent propose, l'humain valide : `sync` montre ce qui part et attend l'accord. Le jeton Git est hors de portée des outils de l'agent (bac à sable de Claude Code, règles de refus d'opencode, qui n'a pas de bac à sable : voir les risques résiduels de [poste-entrepreneur.md](poste-entrepreneur.md)) |
| D-22 | Un **compte assistant par personne** dans Forgejo, comme dans Dokos ; les commits de l'agent sont signés « Assistant de <Prénom> » | Droits délégués et traçabilité |
| D-23 | Deux agents, **Bureau** et **Atelier**, un modèle chacun ; **`small_model` toujours fixé** | Limiter les combinaisons ; éviter qu'un modèle par défaut hébergé par un tiers voie les sessions |
| D-24 | Le modèle est **agnostique** : fournisseur et modèles choisis à l'installation, clé sur le poste seulement ; avec Claude Code, abonnement Claude ou clé API au choix ; avec opencode, clé API | Remplaçabilité ; les abonnements Claude ne s'utilisent qu'avec Claude Code |
| D-25 | Le contexte est **préinstallé** : arborescence 00 à 90, huit fonctions d'une TPE, description des sources de données, modèles de notes, `AGENTS.md` avec huit règles ; aucun savoir inventé | Partir d'un cadre commun, laisser les vrais skills naître des tâches de l'entreprise |
| D-26 | Deux familles de skills : `dokopilot-…` (fournies et mises à jour par DokoPilot, ignorées par Git) et celles de l'entreprise (versionnées, jamais écrasées) | Mettre à jour le produit sans toucher au savoir-faire de l'entreprise |
| D-27 | L'import d'un contexte existant passe par la boîte de réception, trié et relu par un humain, jamais fusionné automatiquement | Un document importé peut contenir des instructions malveillantes |
| D-28 | Le remplissage d'un dépôt de contexte n'a lieu que s'il est vide ; aucune mise à jour automatique d'un contexte existant en version 0.2 | Ne jamais écraser le travail de l'entreprise |

## D-29 — Dans Dokos, seul l'assistant du dirigeant en version 0.2

- **Contexte** : le rôle `Assistant IA` donne les mêmes droits de préparation à tout assistant, quels que soient les droits de la personne qu'il sert. L'assistant d'un membre pourrait donc préparer des factures d'achat même si ce membre n'a pas accès aux achats.
- **Choix** : en version 0.2, seul l'assistant du dirigeant a un utilisateur dans Dokos. Les membres et leurs assistants ont leurs comptes dans Forgejo (contexte) ; le dirigeant crée dans Dokos les comptes de ses salariés avec les rôles qui conviennent. Le harnais d'un membre fonctionne sans serveur MCP Dokos.
- **Revoir si** : on sait dériver les droits de l'assistant de ceux de la personne (droits de la personne ∩ périmètre de l'assistant).

## D-30 — Forgejo : comptes, sauvegarde et version

- **Version** : Forgejo 17.0.1 (image `codeberg.org/forgejo/forgejo:17.0.1-rootless`, figée par empreinte), GPL-3.0 ou plus.
- **Mot de passe initial** : les personnes ne sont pas obligées de le changer à la première connexion. Avec cette obligation, Forgejo refuse Git et l'API, même avec un jeton, tant que le mot de passe n'a pas été changé : le poste ne pourrait pas cloner le contexte juste après l'installation. Le mot de passe initial est remis à la personne par `bin/dokopilot harness` ; elle le change dans Forgejo.
- **Jetons** : portée `write:repository`, limités aux dépôts du compte ; un jeton est remplacé quand ses dépôts changent, ce qui demande de relancer `bin/dokopilot harness`.
- **Sauvegarde** : Forgejo est arrêté quelques secondes et son volume copié en entier avant restic, plutôt qu'un `forgejo dump` ; la restauration est décrite dans exploitation.md.
- **Fonctions coupées** : inscription, courriels, télémétrie, OAuth, webhooks, Actions, paquets, fédération ; les équipes écrivent dans le code, les tickets, les demandes de fusion et le wiki.

## D-31 — Installateur et mode « tout sur cette machine »

- **Seuils** : en mode local, 30 Go libres dans `/var` au minimum (40 conseillés) et au moins 7,5 Gio de mémoire visible, car une VM de 40 Go n'a qu'environ 37 Go libres après Debian ; ces seuils se règlent par variables d'environnement pour les tests.
- **Modèles proposés par défaut** seulement quand leurs identifiants ont été vérifiés : Anthropic (Bureau `claude-sonnet-5-5`, Atelier `claude-opus-5-5`, petit modèle `claude-haiku-5-5`) et Mistral (`mistral-large-4`, sans petit modèle) ; pour les autres fournisseurs, l'assistant demande l'identifiant.
- **Identifiant** : `demo` est refusé par l'assistant (le dossier `clients/demo` est versionné) ; il propose `atelier-demo`.
- **Mode local** : `server.address` vaut `localhost` et le nom de la machine est conservé ; le compte qui lance le script devient compte de maintenance sans que son mot de passe change, sa règle sudo sans mot de passe éventuelle est retirée (sudo demande alors son propre mot de passe), et le rôle refuse un compte dont le mot de passe est verrouillé.
- **Ordre de durcissement** : la règle sudo du groupe de maintenance est écrite avant tout changement de groupe d'un compte ; les réglages SSH sont écrits avant l'installation d'`openssh-server`, pour qu'un serveur SSH nouvellement installé démarre déjà durci.
- **Mots de passe de messagerie et d'agenda** : saisis masqués par l'assistant et écrits sur le poste (`~/.config/dokopilot/<client>/`, 0600), jamais dans les fichiers du client ni dans les secrets du serveur.

## D-32 — Forgejo : branche protégée, pas de miroir, pas de jeton pour les assistants

Après une relecture indépendante de la version 0.2.

- **Branche protégée** : la branche par défaut (`main`) de chaque dépôt de contexte porte une règle de protection, créée par l'installation (avant même le premier commit), rétablie si elle a été retirée ou modifiée, et vérifiée par `check`. Forgejo refuse alors à tout le monde, administrateurs compris, la réécriture (`git push --force`) et la suppression de la branche, avant toute autre règle (`routers/private/hook_pre_receive.go` de la version 17.0.1) ; la poussée reste ouverte à ceux qui ont le droit d'écriture (`enable_push` sans liste d'autorisation). Sans cette règle, n'importe quel jeton en écriture pouvait réécrire l'historique d'`entreprise`, de `direction` ou d'un `perso`. Forgejo refusait déjà la suppression de la branche *par défaut* ; la règle couvre aussi `main` quand l'administrateur d'un dépôt a choisi une autre branche par défaut. La personne, administratrice de son `perso`, peut modifier la règle dans l'interface web (pas avec son jeton, limité aux dépôts) : c'est son dépôt ; `check` le signale et `install` la rétablit.
- **Pas de fichiers verrouillés** : la règle ne déclare aucun motif de fichiers protégés. Verrouiller `AGENTS.md`, `CLAUDE.md`, `.agents/` ou `.gitignore` empêcherait aussi le dirigeant de modifier ses propres règles, ce qui est voulu (le contexte appartient à l'entreprise). Ce qui protège ces fichiers : l'agent ne pousse jamais (D-21, règle 8 d'`AGENTS.md`, `git push` refusé par le harnais) ; l'historique ne peut plus être réécrit, donc toute modification reste visible et réversible (`git revert`) ; et côté poste, `bin/dokopilot sync` et le harnais montrent les modifications venues du serveur sur `AGENTS.md`, `CLAUDE.md`, `.agents/` et `.gitignore` et demandent l'accord de la personne avant de les prendre.
- **Miroirs désactivés** (`[mirror] ENABLED = false`, qui coupe à la fois les miroirs entrants et sortants : `custom/conf/app.example.ini` et `modules/setting/mirror.go` de la version 17.0.1) : sans cela, l'administrateur d'un dépôt pouvait faire pousser automatiquement le contexte vers une adresse extérieure (vérifié sur 17.0.1). Les migrations étaient déjà coupées.
- **Pas de jeton pour les assistants** : le poste n'utilise que le jeton de la personne (C-4, `ansible/roles/harness/tasks/server.yml`) ; un jeton d'assistant ne servait à rien et aurait été un secret de plus à protéger. Un compte assistant garde son mot de passe dans son fichier d'identifiants (root, 0600) mais ne détient aucun jeton ; ceux qu'avait créés une version précédente (nom `dokopilot`) sont supprimés par l'installation, et `check` signale tout jeton d'assistant.
- **Noms de comptes** : le script de provisionnement refuse, avant toute modification de Forgejo, un identifiant égal au compte d'administration ou à l'organisation, une personne dont l'identifiant commence par `assistant-`, un nom réservé par Forgejo (liste `reservedUsernames` de `models/user/user.go` à l'étiquette v17.0.1, chaque nom vérifié sur l'API de 17.0.1) ou un nom que Forgejo refuserait (tiret double ou final). Le rôle `config` fait le même contrôle plus tôt, à la lecture de `client.yml`.
- **Revoir si** : un client veut verrouiller des fichiers (motifs protégés avec une liste d'autorisation au nom du dirigeant), ou Forgejo permet de réécrire une branche protégée (option de réécriture autorisée).

## D-33 — Après la revue de sécurité de novembre 2026

Une revue complète (système, secrets et sauvegardes, pile Dokos, Forgejo, poste, installateur) a conduit aux choix suivants.

- **Secrets déchiffrés** : dans `~/.config/dokopilot/.run/` et non dans `/tmp`. En mode « tout sur cette machine », l'agent tourne sous le même compte que l'intégrateur : le seul dossier que le harnais lui refuse est `~/.config/dokopilot`. Le dossier est nommé d'après le processus qui le détient, effacé sur SIGHUP et SIGTERM, et balayé à la commande suivante si ce processus a disparu.
- **Destinataires age** : un poste n'ajoute jamais sa propre clé à un client existant ; c'est un détenteur des secrets qui l'ajoute, sciemment, avec `--recipient`. `recipients.txt` n'est modifié qu'après un rechiffrement réussi, et la version précédente des secrets est gardée (`secrets.yml.age.bak`).
- **Plages réseau** : validées comme des réseaux (bibliothèque standard, sur le poste qui installe) et non par un motif sur les premiers nombres, qui acceptait `10.0.0.0/1`.
- **`sync` montre ce qu'il envoie** : objets de remplacement neutralisés (`GIT_NO_REPLACE_OBJECTS`) et refusés, comme les greffes, les sous-modules et les dépôts imbriqués ; adresse du serveur limitée au dépôt attendu de Forgejo en HTTP(S) ; règles d'agents reconnues à toute profondeur (liste unique dans `ansible/roles/harness/files/workspace_git.py`) ; différence nette affichée avant l'envoi. Les tests utilisent des dépôts locaux avec la variable `DOKOPILOT_TEST_LOCAL_REMOTES=1`, qui affiche un avertissement.
- **Sauvegardes par installation** : chaque instantané porte `install:<id>` ; la rétention ne touche que les instantanés de l'installation qui la lance, et `latest` ne désigne jamais en silence l'instantané d'un autre serveur. Un serveur de test ou de remplacement monté avec les mêmes réglages partage sinon le dépôt, le nom d'hôte et les chemins de la production, donc son groupe de rétention.
- **Restauration** : Dokos reste fermé après une restauration qui échoue en route (une comptabilité à moitié restaurée ne doit recevoir ni utilisateurs ni tâches planifiées) ; la restauration de Forgejo est outillée (`--forgejo`), l'archive passant par l'entrée standard du conteneur.
- **Rôle « Assistant IA » donné à une personne** : l'installation ne désactive plus que les assistants qu'elle a créés (ceux qui ont un fichier d'identifiants). Ce rôle ne donne que des droits de brouillon : le retirer à un humain n'est pas le travail de l'installation.
- **Non traité, à faire avec la pile réelle** : identifiant 1000 partagé entre les conteneurs et un compte du serveur ([securite.md](securite.md#comptes-du-serveur-et-conteneurs)) ; pièces jointes des documents validés supprimables par l'assistant (rôle `All` de Dokos sur `File`) ; points d'entrée `/api/method` hors MCP ; rapports refusés au rôle de l'assistant ; mise à jour de Dokos sans sauvegarde imposée ; temps réel (Socket.IO). Ces points demandent `make test-stack` et sont décrits dans [revue-2026-11.md](revue-2026-11.md).
