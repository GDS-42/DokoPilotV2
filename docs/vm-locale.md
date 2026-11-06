# Tester sur une VM locale, avec des données de test

Ce guide installe DokoPilot complet sur une machine virtuelle de votre poste, avec le profil **demo** : une entreprise fictive, l'« Atelier Démo », ses clients, fournisseurs, articles, factures, factures électroniques reçues et un relevé bancaire. Comptez une heure, dont l'essentiel en téléchargements et en construction de l'image Dokos.

## 1. Ce qu'il vous faut

| | Minimum |
| --- | --- |
| VM | Debian 13, **amd64** (x86-64), 4 vCPU, **8 Go de RAM**, 40 Go de disque, joignable en SSH depuis votre poste |
| Poste | Linux, macOS ou Windows avec WSL ; Python 3.12 ou plus avec `venv` ; `make` ; `age` ; `ssh` ; une clé SSH ed25519 |
| Réseau | La VM accède à Internet (dépôts Debian, Docker et Tailscale, registre GitLab de Dokos, Docker Hub pour MariaDB et Valkey, GitLab, PyPI, npm) |

L'image officielle de Dokos n'existe qu'en amd64 : sur un Mac Apple Silicon, créez la VM en mode **émulation x86-64** (UTM), ce qui est nettement plus lent, ou utilisez une autre machine.

Sur votre poste :

```bash
sudo apt install python3-venv make age   # macOS : brew install python age
ssh-keygen -t ed25519                    # si vous n'avez pas encore de clé
git clone https://github.com/GDS-42/DokoPilotV2.git && cd DokoPilotV2
make setup                      # Ansible et ses collections dans .venv
bin/dokopilot doctor            # vérifie les outils
```

## 2. Créer la VM

Choisissez l'une des trois méthodes. Retenez le **nom du compte de maintenance** (par défaut votre nom d'utilisateur) et l'**adresse IP** de la VM.

### A. Linux avec libvirt (automatique)

```bash
sudo apt install libvirt-daemon-system virtinst qemu-utils
sudo vm/create-libvirt-vm.sh --user "$USER"
```

Le script (lancé avec sudo, car les disques vont dans `/var/lib/libvirt/images/dokopilot`) télécharge l'image Debian 13 « generic » officielle, vérifie sa somme de contrôle, crée la VM (4 vCPU, 8 Go, 40 Go) avec votre clé et affiche son adresse IP. Il refuse un poste qui n'est pas en x86-64.

### B. VirtualBox, VMware, UTM, Hyper-V ou Proxmox (image cloud et cloud-init)

Sous Windows avec Hyper-V, suivez plutôt le pas à pas dédié : [vm-hyperv.md](vm-hyperv.md) (installation classique de Debian, poste de l'intégrateur dans WSL, Claude Code).

1. Téléchargez `debian-13-generic-amd64.qcow2` depuis `https://cloud.debian.org/images/cloud/trixie/latest/` et vérifiez-le avec `SHA512SUMS`.
2. Convertissez-la si votre hyperviseur l'exige : `qemu-img convert -O vdi …` (VirtualBox), `-O vmdk` (VMware), `-O vhdx` (Hyper-V) ; agrandissez le disque à 40 Go.
3. Créez l'image d'amorçage : `vm/make-seed.sh --user "$USER" --out seed.iso`.
4. Créez une VM de 4 vCPU et 8 Go avec ce disque, attachez `seed.iso` comme lecteur CD, et une carte réseau en **accès par pont** ou sur un **réseau privé hôte** (pour que votre poste joigne la VM).
5. Démarrez : cloud-init crée votre compte avec votre clé SSH. L'adresse IP s'affiche sur la console de la VM (`ip -4 addr`).

### C. Installation classique de Debian 13

1. Installez Debian 13 depuis l'ISO « netinst » amd64, avec le serveur SSH, sans environnement de bureau. Laissez le mot de passe root vide : le compte créé pendant l'installation reçoit sudo. Donnez-lui un nom **différent** de votre compte de maintenance, par exemple `installation` : le premier passage le neutralise (connexion SSH refusée, sudo retiré).
2. Depuis votre poste : `ssh-copy-id installation@<IP>`.
3. Le premier passage se fera avec ce compte (étape 3, variante C).

## 3. Configurer et installer

Connectez-vous une première fois en SSH : vous vérifiez l'empreinte de la VM et attendez que cloud-init ait fini ses installations (méthodes A et B).

```bash
ssh "$USER"@<IP> cloud-init status --wait     # méthode C : ssh installation@<IP> true
```

```bash
bin/dokopilot init demo --address <IP> --maintenance-user "$USER"
bin/dokopilot config demo        # affiche la configuration effective, sans rien changer
```

`init` crée `clients/demo/client.local.yml` (adresse, compte de maintenance, ignoré par Git), votre clé age (`~/.config/dokopilot/age-identity.txt`, à sauvegarder) et les secrets chiffrés `clients/demo/secrets.yml.age`, avec des mots de passe aléatoires.

Variante C seulement : déclarez le compte de l'installation, puis faites le premier passage avec lui (`-K` demande son mot de passe sudo) :

```bash
bin/dokopilot init demo --bootstrap-user installation
bin/dokopilot bootstrap demo -K
```

Puis, dans tous les cas :

```bash
bin/dokopilot install demo
```

L'installation durcit le système (pare-feu, SSH par clé, fail2ban, sudo avec mot de passe), installe Docker, construit l'image Dokos, crée le site, configure l'entreprise, les utilisateurs et Dokos AI, charge les données de test et programme les sauvegardes. Elle se relance sans risque : une seconde exécution ne change rien.

À partir de là, `sudo` sur la VM demande le mot de passe de maintenance : `bin/dokopilot secrets demo show maintenance_sudo_password`.

## 4. Se connecter à Dokos

L'installation affiche l'adresse : en mode `lan`, `http://<IP>`. Comptes :

| Compte | Identifiant | Mot de passe |
| --- | --- | --- |
| Entrepreneur | `camille.martin@atelier-demo.test` | `bin/dokopilot secrets demo show entrepreneur_password` |
| Administrateur | `Administrator` | `bin/dokopilot secrets demo show dokos_admin_password` |
| Assistant | `assistant.camille@atelier-demo.test` | pas de mot de passe : un jeton, utilisé par le harnais |

Vérifiez ensuite : `bin/dokopilot check demo`.

## 5. Brancher un agent

```bash
bin/dokopilot harness demo       # crée ~/DokoPilot/demo et le jeton de l'assistant
cd ~/DokoPilot/demo && opencode  # ou : claude
```

Essayez les scénarios de [donnees-de-test.md](donnees-de-test.md) : « traite les factures fournisseurs reçues », « prépare les relances des impayés », « propose le rapprochement bancaire ». L'agent prépare des brouillons ; vous les validez dans Dokos. Détails : [poste-entrepreneur.md](poste-entrepreneur.md).

## 6. Tester aussi Tailscale (facultatif)

Dans `clients/demo/client.local.yml`, ajoutez `network: { mode: tailscale }`, mettez une clé d'authentification Tailscale dans les secrets (`bin/dokopilot secrets demo edit`, clé `tailscale_auth_key`), relancez `bin/dokopilot install demo`. Dokos n'est alors plus joignable que par `https://<nom-de-la-machine>.<votre-réseau>.ts.net`, depuis un appareil de votre réseau Tailscale.

## 7. Recommencer ou nettoyer

- Recharger les données de test : `bin/dokopilot demo-reset demo`.
- Tester une restauration : `bin/dokopilot backup demo`, puis `bin/dokopilot restore demo --confirm`.
- Supprimer la VM (méthode A) : `sudo virsh -c qemu:///system destroy dokopilot-demo`, `sudo virsh -c qemu:///system undefine dokopilot-demo`, puis `sudo rm -f /var/lib/libvirt/images/dokopilot/dokopilot-demo*` ; oubliez aussi son empreinte SSH : `ssh-keygen -R <IP>`.

## En cas de problème

| Symptôme | Piste |
| --- | --- |
| `Permission denied (publickey)` | Le compte ou la clé ne correspondent pas : vérifiez `client.local.yml` et `ssh <compte>@<IP>`. |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | La VM a été recréée à la même adresse : `ssh-keygen -R <IP>`, puis reconnectez-vous. |
| `make setup` échoue sur `venv` ou la version de Python | Installez `python3-venv` ; il faut Python 3.12 ou plus (Ubuntu 24.04 et Debian 13 conviennent). |
| `Could not get lock /var/lib/dpkg/lock` | cloud-init installe encore des paquets : `ssh <compte>@<IP> cloud-init status --wait`, puis relancez. |
| L'installation s'arrête sur « Debian 13 » ou « x86_64 » | La VM n'est pas une Debian 13 amd64. |
| Construction de l'image interrompue | Mémoire insuffisante : 8 Go de RAM recommandés. Relancez l'installation. |
| Dokos ne répond pas sur `http://<IP>` | Votre poste n'est pas sur une plage privée autorisée (`network.lan.allowed_cidrs`) ou la VM est derrière un NAT sans redirection. |
| `sudo` refuse le mot de passe | Utilisez celui des secrets : `bin/dokopilot secrets demo show maintenance_sudo_password`. |
