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
| D-21 | L'agent écrit **librement** dans la boîte de réception et dans le contexte perso, **sur accord** ailleurs ; il ne pousse jamais : la personne pousse (`bin/dokopilot sync`) | L'agent propose, l'humain valide ; le jeton Git reste hors de portée du shell de l'agent |
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
