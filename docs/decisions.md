# Registre des décisions

Chaque décision indique le contexte, le choix, les options écartées et ce qui la ferait revoir. Les décisions de produit (architecture simplifiée, harnais au choix, auto-hébergement, Tailscale) ont été prises avec le porteur du projet le 2026-11-03 et sont reprises ici ; les décisions techniques ont été tranchées pendant la construction du dépôt, le 2026-11-04, et restent à confirmer par le porteur du projet.

## D-01 — S'appuyer sur les briques existantes

- **Contexte** : la première version (dépôt DokoPilot) prévoyait de développer un harnais, un cockpit et des outils métier. Dokos AI fournit déjà un serveur MCP avec permissions, journal d'audit et validations ; opencode et Claude Code sont des harnais matures.
- **Choix** : ne rien développer côté métier ; ce dépôt se limite à l'infrastructure as code (serveur, Dokos et ses applications, provisionnement, sauvegardes) et à la configuration des harnais sur le poste.
- **Revoir si** : un besoin métier essentiel n'est couvert ni par Dokos, ni par Dokos AI, ni par un connecteur existant.

## D-02 — Ansible et Docker Compose, une commande unique

- **Choix** : Ansible (rôles idempotents) pour le serveur, Docker Compose pour la pile Dokos, un CLI `bin/dokopilot` qui assemble configuration, secrets et inventaire. Un seul fichier `client.yml` par client, fusionné avec les valeurs par défaut du profil dans le fait `dp`, validé avant toute modification.
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
- **Conséquences** : l'entrepreneur et ses postes rejoignent le réseau Tailscale ; un harnais en nuage (application Claude) ne peut pas joindre Dokos, d'où Claude Code sur le poste ; eTransactions et bank ne font que des appels sortants, compatibles.

## D-06 — Accès de maintenance

- **Choix** : comptes nominatifs, clé SSH uniquement, `sudo` avec mot de passe (stocké dans les secrets ; le CLI le fournit à Ansible), pas de connexion root, fail2ban sur SSH, groupe `dokopilot-maint`. Les comptes retirés de la configuration sont verrouillés, pas supprimés.
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
