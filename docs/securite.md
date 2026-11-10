# Sécurité

Ce document décrit ce que l'installation met en place et pourquoi. Les contrôles automatiques
correspondants sont lancés par `bin/dokopilot check <client>` et par les tests du dépôt.

## Exposition du serveur

| Port | Service | Ouvert à | Mode |
| --- | --- | --- | --- |
| SSH (22 par défaut) | Maintenance | Internet, ou les adresses de `maintenance.ssh_allowed_from` | tous |
| HTTPS 443, sur l'interface Tailscale seulement | Dokos (`tailscale serve`) | les appareils autorisés du réseau Tailscale | `network.mode: tailscale` (production) |
| HTTPS 8443, sur l'interface Tailscale seulement | Forgejo (`tailscale serve`) | les appareils autorisés du réseau Tailscale | `tailscale`, si `context.enabled` |
| SSH, sur l'interface Tailscale | Maintenance | les appareils autorisés du réseau Tailscale | `tailscale` |
| HTTP 80 | Dokos (Caddy) | les plages privées de `network.lan.allowed_cidrs` | `network.mode: lan` (démonstration) |
| HTTP 3000 | Forgejo (Caddy) | les plages privées de `network.lan.allowed_cidrs` | `lan`, si `context.enabled` |

Tout le reste est refusé par le pare-feu (nftables, table `inet dokopilot`, politique « drop » en
entrée) ; l'interface de bouclage est toujours acceptée. Aucun port UDP n'est ouvert : Tailscale
établit ses connexions directes depuis le serveur (traversée de NAT) et passe sinon par ses
relais. Les conteneurs ne publient leurs ports que sur 127.0.0.1 (Dokos sur 8080, Forgejo sur
3001) ; la base de données et le cache ne sont jamais publiés.

En production, Dokos et Forgejo n'ont **aucune** adresse publique : même une faille de l'un d'eux
n'est exploitable que depuis un appareil du réseau Tailscale de l'entrepreneur. Réglez dans la
console Tailscale : MagicDNS et certificats HTTPS activés, appareils approuvés manuellement, ACL
limitant l'accès au serveur aux personnes concernées et aux ports 443 et 8443
([installation.md](installation.md)), expiration de clé désactivée pour le serveur seulement.

## Accès de maintenance

- Comptes **nominatifs** (un par intervenant), membres du groupe `dokopilot-maint`, seuls
  autorisés à se connecter en SSH (`AllowGroups`). Leurs groupes secondaires sont exactement ce
  groupe : `sudo`, `adm` ou `docker` sont retirés.
- **Clé SSH uniquement** : mots de passe et clavier interactif désactivés, connexion root
  refusée, 3 essais au plus ; seules les clés déclarées dans la configuration sont dans
  `~/.ssh/authorized_keys`.
- **sudo avec mot de passe** : sur un serveur distant, celui des secrets chiffrés
  (`maintenance_sudo_password`), que `bin/dokopilot` fournit à Ansible ; en mode « tout sur cette
  machine », celui de votre compte (voir plus bas). Journal de sudo dans `/var/log/sudo.log`. Les
  règles sudo sans mot de passe des comptes de maintenance et du compte initial (cloud-init,
  hébergeur) sont retirées. En production, `maintenance.sudo_requires_password: false` est
  refusé : une clé SSH volée ne doit pas donner à elle seule les droits root.
- **Réglages effectifs vérifiés** : l'installation lit les réglages SSH tels qu'ils s'appliquent à
  chaque compte de maintenance (`sshd -T -C user=…`). Si un fichier de l'hébergeur lu avant celui
  de DokoPilot, ou un bloc `Match`, rouvre root ou les mots de passe, elle s'arrête et le dit.
- **fail2ban** bannit les adresses après 5 échecs en 10 minutes, avec une durée qui augmente en
  cas de récidive.
- Le compte initial de l'hébergeur ou de l'installation de Debian (`server.bootstrap_user`, que
  l'assistant renseigne) sert au premier passage (`bootstrap`). Dès ce passage, SSH le refuse ;
  au passage suivant, lancé avec un compte de maintenance, il perd son mot de passe, ses groupes
  et ses clés. root ne se connecte jamais en SSH. Si vous amorcez à la main avec `--user`,
  déclarez ce compte dans `server.bootstrap_user`, sinon il garde son mot de passe et ses règles
  sudo (SSH le refuse tout de même).
- Retirer un intervenant de `client.yml` puis relancer l'installation verrouille son compte et
  vide ses clés.
- Les comptes de maintenance ne sont jamais dans le groupe `docker` (équivalent root) : ils
  passent par sudo.

## Mode « tout sur cette machine »

Quand le serveur est la machine où vous lancez le script (`server.local: true`) :

- **Votre compte devient le compte de maintenance**, sans changer de mot de passe : celui des
  secrets ne lui est jamais appliqué, et sudo vous demande le vôtre (option `-K`, invite
  « BECOME password » d'Ansible). L'installation refuse un compte dont le mot de passe est
  verrouillé ou absent, qui ne pourrait plus utiliser sudo après ce passage.
- **Aucun mot de passe sudo n'est enregistré** : ni le script, ni l'assistant, ni
  `bin/dokopilot` ne le gardent ; Ansible le demande à chaque commande qui en a besoin.
- **Ses règles sudo sans mot de passe sont retirées** (par exemple celle de cloud-init), et ses
  groupes secondaires se réduisent à `dokopilot-maint` : sudo passe par ce groupe, toujours avec
  mot de passe ([D-31](decisions.md)).
- **Le serveur SSH est installé s'il manque**, avec les réglages durcis écrits avant
  l'installation du paquet, pour qu'il démarre déjà durci : clé uniquement, pas de root, groupe
  `dokopilot-maint` seulement. Les clés de votre `~/.ssh/authorized_keys` restent autorisées ;
  une ligne qui porte des options (`from=`, `command=`…) arrête l'assistant, car la clé serait
  réécrite sans elles : retirez la ligne ou ses options. Sans clé, l'assistant vous en demande
  une ou, sur la console, en crée une pour la machine. **Si vous êtes connecté par SSH avec un
  mot de passe**, il exige la clé publique de l'ordinateur que vous utilisez : après
  l'installation, SSH n'accepte plus que les clés, et cette session serait la dernière.
- **Le pare-feu et le durcissement ne coupent pas la session locale** ; Dokos et Forgejo sont
  joignables en `http://localhost` et `http://localhost:3000` depuis la machine elle-même.
- **L'agent tourne sur le serveur**, sous votre compte. Les fichiers de secrets du serveur
  (`/etc/dokopilot`, `.env`) restent lisibles par root seulement (sudo demande votre mot de
  passe), et les données des conteneurs ne se lisent pas depuis votre compte (« Comptes du
  serveur et conteneurs », plus bas) ; la clé age
  (`~/.config/dokopilot/age-identity.txt`), qui déchiffre tous les secrets du client, et les
  jetons sont dans `~/.config/dokopilot`, que le harnais refuse à l'agent, avec les limites
  décrites dans [poste-entrepreneur.md](poste-entrepreneur.md#6-risques-résiduels). sudo ne
  redemande pas le mot de passe pendant 15 minutes après une saisie : lancez l'agent dans un
  autre terminal que celui où vous venez d'utiliser sudo.
- `bin/dokopilot ssh` est inutile et refusé : Ansible se connecte localement.

## Droits des assistants dans Dokos

En version 0.2, seul le dirigeant a un utilisateur « assistant » dans Dokos, dont son harnais
utilise le jeton ([D-29](decisions.md)) : le rôle `Assistant IA` donne les mêmes droits à tout
assistant, quels que soient ceux de la personne servie. Les assistants des membres n'existent que
dans Forgejo, et le dirigeant crée lui-même dans Dokos les comptes de ses salariés, avec les rôles
qui conviennent.

- Rôle `Assistant IA` (créé par l'installation) : lecture et création ou modification de
  **brouillons** sur les documents utiles (achats, ventes, tiers, articles, écritures en
  brouillon) ;
- **jamais** valider, annuler, amender ni supprimer ; jamais System Manager, Administrator ni AI
  Manager ; aucun accès au point de vente ni à la caisse ;
- **les pièces jointes des documents validés sont protégées** : Dokos laisse quiconque peut
  modifier un document supprimer ses fichiers joints, même une fois le document validé.
  L'installation active donc, sur les factures, paiements, écritures, commandes, réceptions,
  livraisons, devis, relances, mouvements de stock et opérations bancaires, la propriété
  « Protéger les fichiers joints » de Dokos : la pièce d'un document validé ne se supprime
  plus, ni par un assistant ni par une personne (celle d'un brouillon se corrige encore) ;
- les rapports comptables dont les compétences se servent (grand livre, balance, registres des
  ventes et des achats, comptes clients et fournisseurs) lui sont ouverts en lecture ;
- les règles de Dokos AI (réglages par type de document, journal d'audit) s'ajoutent à ces
  permissions ;
- les tests `tests/stack/` vérifient, sur une vraie instance, qu'un assistant ne peut ni valider
  ni supprimer une facture, ni supprimer la pièce jointe d'une facture validée.

La validation d'un document reste donc un geste humain dans Dokos, quel que soit le harnais ou le
modèle utilisé.

## Forgejo et les dépôts de contexte

**Exposition.** Forgejo écoute sur `127.0.0.1:3001` seulement ; il n'est publié que par Caddy
(port 3000, réseau local) ou par `tailscale serve` (port 8443), comme Dokos. Son serveur SSH est
désactivé : les copies se font en HTTP(S) seulement.

**Réglages.** Inscription fermée, connexion obligatoire pour tout voir, dépôts toujours privés,
mode hors ligne (aucune ressource extérieure), aucun courriel, ni OAuth, ni OpenID, ni webhooks,
ni Actions, ni paquets, ni fédération, ni recherche de mises à jour, ni migration depuis un autre
serveur ; aucun jeton accepté dans l'adresse d'une requête. Liste complète :
[exploitation.md](exploitation.md#forgejo-dépôts-de-contexte).

**Comptes et droits.**

| Compte | Droits |
| --- | --- |
| `dokopilot-admin` | Administrateur, réservé à l'installation ; mot de passe dans les secrets chiffrés (`forgejo_admin_password`), réappliqué à chaque `install` |
| `<id>`, un par personne | Écriture sur `entreprise` (équipe `membres`), sur `direction` pour le dirigeant (équipe `direction`), administration de son dépôt `perso-<id>` |
| `assistant-<id>`, un par personne qui a un assistant | Compte **restreint** (il ne voit que les dépôts qui lui sont donnés) : écriture sur les mêmes dépôts que sa personne, `perso-<id>` compris |

Un membre et son assistant ne lisent ni `direction` ni le `perso` d'un autre ; `bin/dokopilot
check` vérifie que chaque compte lit ses dépôts et aucun autre. Une personne retirée de
`users.people` voit ses comptes désactivés : connexion interdite, **tous** ses jetons supprimés
(ceux qu'elle avait créés dans l'interface aussi), mot de passe remplacé par un aléa que personne
ne garde ; son dépôt `perso-<id>` est gardé ([exploitation.md](exploitation.md)). Seul
`dokopilot-admin` administre Forgejo : un compte déclaré qu'on aurait promu administrateur est
rétrogradé par `install`.

**Jetons.** Chaque compte de personne a un jeton Git de portée `write:repository`, limité à ses
dépôts ; il est remplacé quand ces dépôts changent ([D-30](decisions.md)), et il faut alors
relancer `bin/dokopilot harness` sur le poste. Les comptes d'assistant n'ont aucun jeton : rien ne
s'en sert, l'agent ne poussant jamais ([D-32](decisions.md)).

- Sur le serveur : `/etc/dokopilot/forgejo/<compte>.json` (`user`, `password`, et `token` pour
  une personne), lisible par root seulement (fichier 0600, dossier 0700).
- Sur le poste : `bin/dokopilot harness` lit, avec sudo et sans rien afficher, le fichier de la
  **personne** seulement, et écrit son jeton dans `~/.config/dokopilot/<client>/git-credentials`
  et son mot de passe initial dans `forgejo-password` (fichiers 0600, dossier 0700), **hors du
  dossier de travail**. Git les lit par son assistant d'identifiants `store`, configuré dans
  chaque copie : aucun jeton dans l'adresse d'un dépôt ni dans `.git/config`.

**Mot de passe initial, sans changement forcé.** Chaque personne reçoit un mot de passe initial
aléatoire de 24 caractères. Forgejo ne l'oblige pas à le changer à la première connexion : avec
cette obligation, Forgejo refuse Git et l'API, même avec un jeton, tant que le mot de passe n'a
pas été changé, et le poste ne pourrait pas copier le contexte juste après l'installation
([D-30](decisions.md)). La personne le change elle-même dans Forgejo ; le jeton Git n'en dépend
pas.

## L'agent ne pousse jamais

L'agent commite dans les copies du poste, au nom de l'assistant de la personne ; il ne pousse
jamais : `git push`, `git config`, `git remote` et `git credential` lui sont refusés par le
harnais, et le jeton Git est hors de sa portée (`~/.config/dokopilot` refusé à la lecture ; bac à
sable du système avec Claude Code). C'est la personne qui envoie, avec `bin/dokopilot sync`
([D-21](decisions.md)).

**Ce que vérifie `bin/dokopilot sync`**, copie par copie (`entreprise`, `direction`, `perso`),
avant d'envoyer quoi que ce soit :

- la configuration Git locale de la copie ne contient que des réglages attendus (un réglage qui
  lancerait un programme ou enverrait ailleurs, qu'un agent aurait pu écrire, arrête tout) ;
- l'assistant d'identifiants est exactement celui de DokoPilot, sur le fichier
  `git-credentials` de ce client ;
- la copie a une seule adresse de serveur : le dépôt attendu (`<client>/entreprise`,
  `<client>/direction` ou `<client>/perso-<id>`) sur le Forgejo inscrit dans ce fichier, en
  HTTP(S), sans identifiants dans l'adresse ; une adresse SSH, `git://`, un dossier local ou un
  autre dépôt du même serveur sont refusés ;
- la copie ne contient ni objet de remplacement (`git replace`), ni greffe d'historique, ni
  sous-module, ni dépôt Git dans un sous-dossier : ils permettraient de montrer un contenu et
  d'en envoyer un autre, ou de lancer un programme à votre prochaine commande git ;
- aucun conflit ni rebasage n'est en cours, et aucun fichier suivi n'est modifié sans être
  enregistré (les fichiers nouveaux non enregistrés restent sur le poste, signalés) ;
- aucune branche ne suit un autre serveur que `origin`, et le dossier `.git` ne renvoie pas
  ailleurs.

Ensuite, `sync` **montre** à la personne ce qu'il va envoyer (auteur, message et fichiers de
chaque enregistrement, puis la différence nette avec le serveur, et un avertissement si l'envoi
change des règles d'agents) et attend son accord (`--yes` l'en dispense) ; sans accord, rien
n'est envoyé. Les caractères de contrôle d'un message ou d'un nom de fichier ne sont jamais
affichés tels quels. Les règles des agents reçues du serveur (`AGENTS.md`, `CLAUDE.md`,
`CLAUDE.local.md`, `.agents/`, `.claude/`, `.opencode/`, `opencode.json`, `.mcp.json`,
`.gitignore`, `.gitattributes`, **à n'importe quelle profondeur** : les harnais chargent aussi
les règles d'un sous-dossier) sont montrées et doivent être acceptées, même avec `--yes`, et
`bin/dokopilot harness` ne les prend jamais en silence : un membre, ou son agent, ne peut pas
changer les règles de l'agent du dirigeant sans que celui-ci les voie. Forgejo refuse en outre
toute réécriture (force push) et toute suppression de la branche principale des dépôts.

Puis il lance `git pull --rebase` et `git push` sans les crochets (hooks) ni le fsmonitor de la
copie, sans les variables `GIT_*` de la session, sans objets de remplacement
(`GIT_NO_REPLACE_OBJECTS`), sans jamais demander de mot de passe, avec les seuls identifiants de
la personne, et s'arrête au premier problème. Les commandes git qu'il conseille de taper portent
les mêmes options. Il vérifie la copie, pas son
contenu : ce qu'il envoie est ce que l'agent a commité, relisez-le
([poste-entrepreneur.md](poste-entrepreneur.md#6-risques-résiduels)).

## Secrets

- Sur le poste qui installe : `clients/<client>/secrets.yml.age`, chiffré avec age pour les clés
  de `recipients.txt` (intégrateur, entrepreneur, clé de secours conservée hors ligne). Votre clé
  age privée est dans `~/.config/dokopilot/age-identity.txt` : sauvegardez-la dans un
  gestionnaire de mots de passe.
- Pendant une commande, les secrets déchiffrés n'existent que dans un dossier privé de
  `~/.config/dokopilot/.run/` (0700, à côté de la clé age, dans le dossier que le harnais refuse
  aux agents ; jamais dans `/tmp`), effacé à la fin, y compris sur Ctrl-C, terminal fermé ou
  session SSH perdue ; ce qu'une coupure de courant aurait laissé est effacé à la commande
  suivante. Ansible ne les affiche jamais (`no_log`).
- Les destinataires des secrets (`recipients.txt`) ne changent qu'à la demande : `bin/dokopilot
  init <client> --recipient <clé>` valide la clé, rechiffre, puis écrit le fichier. Un autre
  poste n'ajoute jamais sa propre clé à un client existant. Retirer une ligne ne suffit pas à
  révoquer quelqu'un qui a déjà lu les secrets : changez alors les mots de passe concernés.
- L'assistant d'installation n'écrit aucun secret en clair dans `clients/` : la clé Tailscale et
  les identifiants de sauvegarde vont dans les secrets chiffrés, et son fichier d'état
  (`.assistant-etat.json`) n'en contient jamais. Un fichier de réponses (`--answers`) ne peut pas
  en contenir : l'assistant refuse un secret écrit en clair et attend le nom d'une variable
  d'environnement ou d'un fichier.
- La clé du fournisseur de modèle et les mots de passe de messagerie et d'agenda restent sur le
  poste, dans `~/.config/dokopilot/<client>/` (fichiers 0600) : jamais dans les fichiers du
  client ni dans les secrets du serveur ([D-31](decisions.md)).
- Sur le serveur : fichiers `/etc/dokopilot/*` (dont les identifiants de Forgejo et les jetons des
  assistants) et `.env` des projets Compose, lisibles par root seulement.
- Le mot de passe du dépôt de sauvegarde doit aussi être conservé **hors du serveur** : sans lui,
  une sauvegarde est inutilisable. En mode « tout sur cette machine », la clé age, les secrets
  chiffrés et ce mot de passe sont sur le même disque que les données : gardez-en une copie
  ailleurs (le résumé de fin d'installation le rappelle).

### Comptes du serveur et conteneurs

Linux laisse un processus lire les fichiers et l'environnement d'un autre par `/proc` quand ils
ont le même utilisateur **et** le même groupe. Or le premier compte créé sur une machine porte en
général l'identifiant 1000 (uid et gid), comme l'utilisateur des conteneurs : souvent le premier
compte de maintenance, ou votre compte en mode « tout sur cette machine », sous lequel l'agent
tourne.

- **Forgejo** : l'image tourne sous 1000:1000. Sans précaution, un tel compte lisait sans sudo la
  base, `app.ini` et tous les dépôts, `direction` compris (reproduit sur l'image réelle).
  L'installation lance donc le conteneur avec un groupe principal que n'a aucun compte
  (`dokopilot-ct`, groupe système sans membre), le groupe de l'image restant secondaire.
- **Dokos** : l'image officielle tourne sous l'utilisateur 1000 avec le groupe `root` (0), que
  n'a aucun compte ordinaire : rien à changer.

Les tests de la pile réelle (`tests/context/run.sh`, `tests/stack/run.sh`) vérifient pour chaque
conteneur qu'un processus d'uid et de gid 1000 ne lit ni ses fichiers ni son environnement. Il
reste qu'un compte d'uid 1000 peut envoyer un signal à ces processus (les arrêter) : sur un
serveur distant, n'ouvrez de session de maintenance qu'à des personnes de confiance.

## Poste et agents

Voir [poste-entrepreneur.md](poste-entrepreneur.md). En résumé : le shell de l'agent reste
disponible, mais les effets sont validés (brouillons dans Dokos, brouillons de courriels sans
aucun envoi possible, écriture libre seulement dans la boîte de réception et `perso/`, aucun envoi
vers Forgejo) ; après la lecture d'un courriel, la session devient « exposée » et chaque action à
effet repasse par une confirmation ; le contenu d'un courriel est une donnée, jamais une
instruction. Les **risques résiduels** (jetons lisibles par une commande détournée avec opencode,
commandes de l'Atelier, jeton Git en clair sur le réseau local en mode `lan`, approbations
durables…) sont listés dans
[poste-entrepreneur.md](poste-entrepreneur.md#6-risques-résiduels).

## Mises à jour de sécurité

`unattended-upgrades` applique chaque nuit les correctifs de Debian, de Docker et de Tailscale ;
le redémarrage automatique est désactivé par défaut (`base_automatic_reboot`), `needrestart`
signale les services à relancer. Les versions de Dokos, de ses applications et de Forgejo sont
figées par empreinte et ne changent que par une mise à jour explicite
([exploitation.md](exploitation.md)).
