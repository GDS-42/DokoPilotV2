# DokoPilot V2

Installation unifiée, en infrastructure as code, d'un environnement de gestion agentique pour une TPE ou PME française :

- **Dokos v5 non modifié** (ERP libre), avec ses applications **Dokos AI** (serveur MCP, validations, journal) et **eTransactions** (facturation électronique par la plateforme agréée Super PDP) ; les relevés bancaires sont importés dans Dokos ;
- sur un **serveur auto-hébergé au nom de l'entrepreneur** (VPS Debian 13), durci, joignable **uniquement par Tailscale**, avec un accès de maintenance SSH nominatif (clé, sudo, fail2ban) et des **sauvegardes chiffrées** ;
- et, sur le **poste de l'entrepreneur**, un **harnais d'agent au choix** (opencode par défaut, Claude Code en option) branché sur Dokos par MCP, avec des compétences métier en français.

L'agent prépare, l'humain valide : l'utilisateur « assistant » de chaque personne lit et crée des brouillons dans Dokos, mais ne peut ni valider, ni annuler, ni supprimer un document. La caisse certifiée n'est pas concernée.

Tout se règle dans **un seul fichier par client** (`clients/<client>/client.yml`) et **un fichier de secrets chiffré** (`secrets.yml.age`) ; une seule commande installe ou met à jour l'ensemble.

## Démarrage rapide : tester sur une VM locale avec des données de test

Il vous faut un poste Linux, macOS ou Windows avec WSL, Python 3.12+ avec `venv`, `make`, `age`, une clé SSH (`ssh-keygen -t ed25519`) et une VM Debian 13 amd64 de 4 vCPU, 8 Go de RAM et 40 Go de disque ([docs/vm-locale.md](docs/vm-locale.md) explique comment la créer).

```bash
git clone https://github.com/GDS-42/DokoPilotV2.git && cd DokoPilotV2
make setup                                   # Ansible et ses collections dans .venv
bin/dokopilot init demo --address <IP-de-la-VM> --maintenance-user <votre-nom>
bin/dokopilot config demo                    # vérifie la configuration, sans rien changer
bin/dokopilot install demo                   # installe tout, données de test comprises
bin/dokopilot check demo                     # contrôles de fonctionnement et de sécurité
bin/dokopilot harness demo                   # configure opencode / Claude Code sur votre poste
```

À la fin, l'installation affiche l'adresse de Dokos et les comptes de démonstration. Les données de test décrivent une entreprise fictive, l'« Atelier Démo » : clients, fournisseurs, articles, factures et commandes, factures électroniques reçues et relevé bancaire d'exemple ([docs/donnees-de-test.md](docs/donnees-de-test.md)).

## Installer chez un client

1. L'entrepreneur loue un VPS Debian 13 à son nom et crée un compte Tailscale ([docs/installation.md](docs/installation.md)).
2. `bin/dokopilot init <client> --profile production`, puis complétez `clients/<client>/client.yml` et les secrets (`bin/dokopilot secrets <client> edit` : clé Tailscale, dépôt de sauvegarde, identifiants Super PDP).
3. `bin/dokopilot bootstrap <client>` avec le compte fourni par l'hébergeur, puis `bin/dokopilot install <client>`.
4. Sur le poste de l'entrepreneur : `bin/dokopilot harness <client>` ([docs/poste-entrepreneur.md](docs/poste-entrepreneur.md)).

Claude Code peut piloter ces étapes depuis le poste de l'intégrateur : il lit [`CLAUDE.md`](CLAUDE.md).

## Documentation

| Document | Contenu |
| --- | --- |
| [Architecture](docs/architecture.md) | Les briques, les flux, ce qui tourne où |
| [Décisions](docs/decisions.md) | Les choix tranchés et leurs raisons |
| [VM locale](docs/vm-locale.md) | Créer la VM de test et y installer la démo |
| [Installation](docs/installation.md) | Mise en production chez un client |
| [Sécurité](docs/securite.md) | Exposition, accès de maintenance, droits des assistants, secrets |
| [Exploitation](docs/exploitation.md) | Sauvegardes, restauration, mises à jour, supervision |
| [Données de test](docs/donnees-de-test.md) | Ce que contient la démo, comment la recharger |
| [Poste de l'entrepreneur](docs/poste-entrepreneur.md) | Harnais, connecteurs messagerie et agenda, garde-fous |

## Licence

AGPL-3.0-or-later. Dokos, Dodock et les applications Dokos sont des logiciels libres distincts, utilisés sans modification sous leurs propres licences. DokoPilot n'est pas un produit de Dokos SAS et ne détient aucune certification ni aucun agrément.
