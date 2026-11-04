# Sécurité

Ce document décrit ce que l'installation met en place et pourquoi. Les contrôles automatiques correspondants sont lancés par `bin/dokopilot check <client>` et par les tests du dépôt.

## Exposition du serveur

| Port | Ouvert à | Profil |
| --- | --- | --- |
| SSH (22 par défaut) | Internet, ou les adresses de `maintenance.ssh_allowed_from` | tous |
| HTTPS 443 sur l'interface Tailscale | les appareils autorisés du réseau Tailscale | production (`network.mode: tailscale`) |
| UDP 41641 | Internet (connexions directes Tailscale, chiffrées WireGuard) | production |
| HTTP 80 | les plages privées de `network.lan.allowed_cidrs` | demo (`network.mode: lan`) uniquement |

Tout le reste est refusé par le pare-feu (nftables, table `inet dokopilot`, politique « drop » en entrée). Les conteneurs Dokos ne publient leurs ports que sur 127.0.0.1 ; la base de données et le cache ne sont jamais publiés.

En production, Dokos n'a **aucune** adresse publique : même une faille de Dokos n'est exploitable que depuis un appareil du réseau Tailscale de l'entrepreneur. Réglez dans la console Tailscale : MagicDNS et certificats HTTPS activés, appareils approuvés manuellement, ACL limitant l'accès au serveur aux personnes concernées, expiration de clé désactivée pour le serveur seulement.

## Accès de maintenance

- Comptes **nominatifs** (un par intervenant), membres du groupe `dokopilot-maint`, seuls autorisés à se connecter en SSH.
- **Clé SSH uniquement** : mots de passe et clavier interactif désactivés, connexion root refusée, 3 essais au plus.
- **sudo avec mot de passe** (dans les secrets chiffrés) ; journal de sudo dans `/var/log/sudo.log`.
- **fail2ban** bannit les adresses après 5 échecs en 10 minutes, avec une durée qui augmente en cas de récidive.
- Retirer un intervenant de `client.yml` puis relancer l'installation verrouille son compte et vide ses clés.
- Les comptes de maintenance ne sont jamais dans le groupe `docker` (équivalent root) : ils passent par sudo.

## Droits des assistants dans Dokos

Chaque personne qui utilise un agent dispose d'un utilisateur « assistant » distinct, dont le harnais utilise le jeton :

- rôle `Assistant IA` (créé par l'installation) : lecture et création ou modification de **brouillons** sur les documents utiles (achats, ventes, tiers, articles, écritures en brouillon) ;
- **jamais** valider, annuler, amender ni supprimer ; jamais System Manager, Administrator ni AI Manager ; aucun accès au point de vente ni à la caisse ;
- les règles de Dokos AI (réglages par type de document, journal d'audit) s'ajoutent à ces permissions ;
- les tests `tests/stack/` vérifient, sur une vraie instance, qu'un assistant ne peut ni valider ni supprimer une facture.

La validation d'un document reste donc un geste humain dans Dokos, quel que soit le harnais ou le modèle utilisé.

## Secrets

- Sur le poste de l'intégrateur : `clients/<client>/secrets.yml.age`, chiffré avec age pour les clés de `recipients.txt` (intégrateur, entrepreneur, clé de secours conservée hors ligne). Votre clé age privée est dans `~/.config/dokopilot/age-identity.txt` : sauvegardez-la dans un gestionnaire de mots de passe.
- Pendant une installation, les secrets déchiffrés n'existent que dans un dossier temporaire privé, effacé à la fin ; Ansible ne les affiche jamais (`no_log`).
- Sur le serveur : fichiers `/etc/dokopilot/*` et `.env` de la pile, lisibles par root seulement.
- Le mot de passe du dépôt de sauvegarde doit aussi être conservé **hors du serveur** : sans lui, une sauvegarde est inutilisable.
- Les mots de passe de messagerie et d'agenda de l'entrepreneur ne quittent pas son poste.

## Poste de l'entrepreneur et agents

Voir [poste-entrepreneur.md](poste-entrepreneur.md). En résumé : le shell de l'agent reste disponible, mais les effets sont validés (brouillons dans Dokos, brouillons de courriels jamais envoyés sans accord) ; après la lecture d'un courriel, la session devient « exposée » et chaque action à effet repasse par une confirmation ; le contenu d'un courriel est une donnée, jamais une instruction.

## Mises à jour de sécurité

`unattended-upgrades` applique chaque nuit les correctifs de Debian, de Docker et de Tailscale ; le redémarrage automatique est désactivé par défaut (`base_automatic_reboot`), `needrestart` signale les services à relancer. Les versions de Dokos et de ses applications sont figées et ne changent que par une mise à jour explicite ([exploitation.md](exploitation.md)).
