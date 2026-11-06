# Tester sur une VM locale, avec des données de test

Ce guide installe DokoPilot complet sur une machine virtuelle de votre poste, avec le profil
**demo** : une entreprise fictive, l'« Atelier Démo », ses clients, fournisseurs, articles,
factures, factures électroniques reçues, un relevé bancaire, et son contexte dans Forgejo.
Comptez une heure, dont l'essentiel en téléchargements et en construction de l'image Dokos.

Deux façons de faire, avec le même script :

- **A. Tout sur la VM** : une seule commande, lancée dans la VM ; l'agent tourne aussi dans la VM.
- **B. La VM comme serveur distant** : le script tourne sur votre poste et pilote la VM en SSH ;
  l'agent tourne sur votre poste.

## 1. Ce qu'il vous faut

| | Minimum |
| --- | --- |
| VM | Debian 13, **amd64** (x86-64), 4 vCPU, **8 Go de RAM** (au moins 7,5 Gio visibles dans la VM), 40 Go de disque (façon A : au moins 30 Go libres dans `/var`) |
| Poste (façon B) | Debian 13, Ubuntu 24.04 ou plus récent (WSL compris) ou Fedora 41 ou plus récent ; une clé SSH ed25519 (l'assistant propose de la créer) |
| Réseau | La VM accède à Internet (dépôts Debian et Docker, registre GitLab de Dokos, Docker Hub pour MariaDB et Valkey, Codeberg pour Forgejo, GitHub, PyPI, Ansible Galaxy, npm) ; votre poste joint la VM |

L'image officielle de Dokos n'existe qu'en amd64 : sur un Mac Apple Silicon, créez la VM en mode
**émulation x86-64** (UTM), ce qui est nettement plus lent, ou utilisez une autre machine. Le
script ne s'utilise pas depuis macOS : avec un Mac comme poste, prenez la façon A, ou le chemin
manuel ([installation.md](installation.md), partie 2).

## 2. Créer la VM

Choisissez l'une des trois méthodes. Retenez l'**adresse IP** de la VM. Les méthodes A et B
utilisent les scripts du dossier `vm/` : récupérez d'abord DokoPilot sur votre poste (le script
d'installation réutilisera cette copie) :

```bash
git clone --branch v0.2.0 https://github.com/GDS-42/DokoPilotV2.git ~/DokoPilotV2
cd ~/DokoPilotV2
```

### A. Linux avec libvirt (automatique)

```bash
sudo apt install libvirt-daemon-system virtinst qemu-utils
sudo vm/create-libvirt-vm.sh --user "$USER"
```

Le script (lancé avec sudo, car les disques vont dans `/var/lib/libvirt/images/dokopilot`)
télécharge l'image Debian 13 « generic » officielle, vérifie sa somme de contrôle, crée la VM
(4 vCPU, 8 Go, 40 Go) avec votre compte et votre clé et affiche son adresse IP. Il refuse un poste
qui n'est pas en x86-64.

### B. VirtualBox, VMware, UTM, Hyper-V ou Proxmox (image cloud et cloud-init)

Sous Windows avec Hyper-V, suivez plutôt le pas à pas dédié : [vm-hyperv.md](vm-hyperv.md).

1. Téléchargez `debian-13-generic-amd64.qcow2` depuis
   `https://cloud.debian.org/images/cloud/trixie/latest/` et vérifiez-le avec `SHA512SUMS`.
2. Convertissez-la si votre hyperviseur l'exige : `qemu-img convert -O vdi …` (VirtualBox),
   `-O vmdk` (VMware), `-O vhdx` (Hyper-V) ; agrandissez le disque à 40 Go.
3. Créez l'image d'amorçage : `vm/make-seed.sh --user "$USER" --out seed.iso`.
4. Créez une VM de 4 vCPU et 8 Go avec ce disque, attachez `seed.iso` comme lecteur CD, et une
   carte réseau en **accès par pont** ou sur un **réseau privé hôte** (pour que votre poste joigne
   la VM).
5. Démarrez : cloud-init crée votre compte avec votre clé SSH. L'adresse IP s'affiche sur la
   console de la VM (`ip -4 addr`).

Avec les méthodes A et B, votre compte dans la VM a sudo sans mot de passe et **aucun mot de
passe** : la façon A en demande un (étape 3).

### C. Installation classique de Debian 13

Installez Debian 13 depuis l'ISO « netinst » amd64, sans environnement de bureau, avec le serveur
SSH. Laissez le mot de passe root vide : le compte créé pendant l'installation reçoit sudo.

- Pour la façon A, créez **votre** compte (votre prénom, en minuscules) : il deviendra le compte
  de maintenance, avec son mot de passe.
- Pour la façon B, créez un compte `installation`, différent de votre compte de maintenance : le
  premier passage s'en sert, puis le neutralise (connexion SSH refusée, sudo retiré).

## 3. Façon A : tout sur la VM, en une commande

Depuis votre poste, copiez votre clé SSH dans la VM puis connectez-vous (méthode C :
`ssh-copy-id <compte>@<IP>` d'abord ; méthodes A et B : la clé y est déjà) :

```bash
ssh <compte>@<IP>
```

Gardez votre clé dans `~/.ssh/authorized_keys` de la VM : après l'installation, SSH n'accepte plus
que les clés, et l'assistant garde celles qui y sont.

Dans la VM :

```bash
cloud-init status --wait          # méthodes A et B : attendre la fin du premier démarrage
sudo passwd "$USER"               # méthodes A et B : donner un mot de passe à votre compte
command -v curl || sudo apt install -y curl
curl -fsSL https://raw.githubusercontent.com/GDS-42/DokoPilotV2/v0.2.0/install.sh | sh
```

Répondez aux questions ([installation.md](installation.md) les détaille) : **`local`** (tout sur
cette machine), **`demo`**, identifiant `atelier-demo`, l'agent, le fournisseur de modèle et sa
clé, puis Entrée pour garder l'entreprise et la personne proposées (Camille Martin), et
**`lan`**. À chaque invite `BECOME password:`, tapez le mot de passe de votre compte dans la VM.

## 4. Façon B : la VM pilotée depuis votre poste

Sur votre poste :

```bash
curl -fsSL https://raw.githubusercontent.com/GDS-42/DokoPilotV2/v0.2.0/install.sh | sh
```

Répondez **`distant`**, puis :

| Question | Méthodes A et B | Méthode C |
| --- | --- | --- |
| Adresse du serveur | l'IP de la VM | l'IP de la VM |
| Compte initial du serveur | votre compte (cloud-init l'a créé avec votre clé) | `installation` |
| Votre compte de maintenance | votre compte | votre compte |
| `BECOME password:` au premier passage | Entrée (sudo sans mot de passe) | le mot de passe du compte `installation` |

Si le compte initial n'accepte pas encore votre clé, l'assistant propose de l'y copier
(`ssh-copy-id` demande alors son mot de passe). Ensuite, comme pour la façon A : `demo`,
`atelier-demo`, l'agent, le modèle, l'entreprise proposée, `lan`. Après l'installation, sudo sur
la VM demande le mot de passe de maintenance des secrets :
`bin/dokopilot secrets atelier-demo show maintenance_sudo_password`.

## 5. Ce que fait l'installation

Elle durcit le système (pare-feu, SSH par clé, fail2ban, sudo avec mot de passe), installe
Docker, construit l'image Dokos, crée le site, configure l'entreprise, les utilisateurs et Dokos
AI, charge les données de test, installe Forgejo avec les dépôts de contexte, programme les
sauvegardes, puis configure l'agent dans `~/DokoPilot/atelier-demo` et lance les contrôles. Elle
dure de 30 à 60 minutes et se relance sans risque : si elle s'arrête, corrigez la cause indiquée
et relancez la commande `curl` (ou `sh install.sh`) ; l'assistant reprend où il s'était arrêté.

À la fin, l'assistant affiche les adresses, les comptes et ce qui manque encore sur le poste :
installez l'agent choisi ([poste-entrepreneur.md](poste-entrepreneur.md), « 1. Préparer
l'ordinateur »). **Sauvegardez `~/.config/dokopilot/age-identity.txt`** : sans elle, les secrets
de la démo sont illisibles.

## 6. Vérifier

Les commandes `bin/dokopilot` se lancent depuis `~/DokoPilotV2` : dans la VM pour la façon A,
sur votre poste pour la façon B.

**Contrôles.** Façon A : `bin/dokopilot check atelier-demo --local -K` (votre mot de passe à
l'invite `BECOME password:`). Façon B : `bin/dokopilot check atelier-demo`. La commande échoue au
moindre écart.

**Dokos.** Depuis votre poste : `http://<IP>` (dans la VM : `http://localhost`).

| Compte | Identifiant | Mot de passe |
| --- | --- | --- |
| Entrepreneur | `camille.martin@atelier-demo.test` | `bin/dokopilot secrets atelier-demo show entrepreneur_password` |
| Administrateur | `Administrator` | `bin/dokopilot secrets atelier-demo show dokos_admin_password` |
| Assistant | `assistant.camille@atelier-demo.test` | pas de mot de passe : un jeton, utilisé par le harnais |

**Forgejo.** Depuis votre poste : `http://<IP>:3000` (dans la VM : `http://localhost:3000`).
Connectez-vous avec le compte `camille` et le mot de passe initial de
`~/.config/dokopilot/atelier-demo/forgejo-password` (sur la machine où tourne l'agent), puis
changez-le. L'organisation `atelier-demo` contient `entreprise`, `direction` et `perso-camille`.

**L'espace de travail.** Sur la machine où tourne l'agent :

```bash
ls ~/DokoPilot/atelier-demo     # AGENTS.md, 00-Boite-de-reception … 90-Modeles, direction/, perso/
git -C ~/DokoPilot/atelier-demo log --oneline -1   # « Contexte initial préparé par DokoPilot »
```

**L'envoi vers Forgejo.** Enregistrez une note dans Git, puis envoyez-la :

```bash
cd ~/DokoPilot/atelier-demo
echo "# Essai de synchronisation" > 00-Boite-de-reception/essai.md
git add 00-Boite-de-reception/essai.md && git commit -m "Ajoute une note d'essai"
~/DokoPilotV2/bin/dokopilot sync atelier-demo
```

`sync` montre l'enregistrement à envoyer et demande votre accord (tapez `o`), puis affiche une
ligne par dépôt (`✓ entreprise : 1 envoyé(s)`, `✓ direction : déjà à jour`,
`✓ perso : déjà à jour`) ; la note apparaît dans Forgejo, dans `atelier-demo/entreprise`. Il
refuse d'envoyer un fichier modifié qui n'a pas été enregistré (`git commit`) et dit quoi faire.

## 7. Essayer l'agent

```bash
cd ~/DokoPilot/atelier-demo && opencode      # ou : claude
```

Essayez les scénarios de [donnees-de-test.md](donnees-de-test.md) : « traite les factures
fournisseurs reçues », « prépare les relances des impayés », « propose le rapprochement
bancaire ». L'agent prépare des brouillons ; vous les validez dans Dokos. Demandez-lui aussi
d'écrire une note dans la boîte de réception : il l'écrit sans demander (la boîte de réception est
libre), propose de la commiter au nom d'« Assistant de Camille », et refuse de l'envoyer ; c'est
vous qui l'envoyez avec `bin/dokopilot sync`. Détails :
[poste-entrepreneur.md](poste-entrepreneur.md).

## 8. Tester aussi Tailscale (facultatif)

Choisissez `tailscale` à la question du réseau (une clé d'authentification est demandée), ou
passez-y après coup : ajoutez `network: { mode: tailscale }` dans
`clients/atelier-demo/client.local.yml`, mettez une clé d'authentification Tailscale dans les
secrets (`bin/dokopilot secrets atelier-demo edit`, clé `tailscale_auth_key`), puis relancez
`bin/dokopilot install atelier-demo` et `bin/dokopilot harness atelier-demo` (avec `--local -K`
pour la façon A ; en façon B, votre poste doit être connecté au même réseau Tailscale). Dokos
n'est alors joignable que par `https://dokos-atelier-demo.<votre-réseau>.ts.net` et Forgejo par
le même nom, port 8443, depuis un appareil de votre réseau Tailscale
([installation.md](installation.md), « Régler Tailscale »).

## 9. Recommencer ou nettoyer

Façon A : ajoutez `--local -K` à chaque commande ci-dessous.

- Recharger les données de test : `bin/dokopilot demo-reset atelier-demo`.
- Tester une sauvegarde et une restauration : `bin/dokopilot backup atelier-demo`, puis
  `bin/dokopilot restore atelier-demo --confirm` (Forgejo se restaure à part :
  [exploitation.md](exploitation.md#sauvegarde-et-restauration)).
- Supprimer la VM (méthode A) : `sudo virsh -c qemu:///system destroy dokopilot-demo`,
  `sudo virsh -c qemu:///system undefine dokopilot-demo`, puis
  `sudo rm -f /var/lib/libvirt/images/dokopilot/dokopilot-demo*` ; oubliez aussi son empreinte
  SSH : `ssh-keygen -R <IP>`.

**Le chemin manuel**, sans assistant, reste possible (par exemple depuis un Mac) avec l'exemple
versionné `clients/demo` : `make setup`, puis
`bin/dokopilot init demo --address <IP> --maintenance-user "$USER"`, `bin/dokopilot config demo`,
`bin/dokopilot install demo`, `bin/dokopilot harness demo` et `bin/dokopilot check demo`. Avec la
méthode C, ajoutez `--bootstrap-user installation` à `init` et lancez
`bin/dokopilot bootstrap demo -K` avant `install` ([installation.md](installation.md), partie 2).

## En cas de problème

| Symptôme | Piste |
| --- | --- |
| `install.sh` : « Système non pris en charge » ou Python trop ancien | Le poste doit être Debian 13, Ubuntu 24.04 ou plus récent, ou Fedora 41 ou plus récent (Python 3.12 ou plus) |
| « Tout sur cette machine » impossible ici | L'assistant donne la raison : pas Debian 13, pas amd64, moins de 7,5 Gio de mémoire, moins de 30 Go libres dans `/var`, compte root ou non nominatif, sites injoignables |
| `install` : le compte « … » n'a pas de mot de passe utilisable | Façon A sur une image cloud : `sudo passwd "$USER"`, puis relancez |
| `Permission denied (publickey)` | Le compte ou la clé ne correspondent pas : vérifiez `clients/atelier-demo/client.local.yml` et `ssh <compte>@<IP>` |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | La VM a été recréée à la même adresse : `ssh-keygen -R <IP>`, puis reconnectez-vous |
| `Could not get lock /var/lib/dpkg/lock` | cloud-init installe encore des paquets : `cloud-init status --wait` dans la VM, puis relancez |
| L'installation s'arrête sur « Debian 13 » ou « x86_64 » | La VM n'est pas une Debian 13 amd64 |
| Construction de l'image interrompue | Mémoire insuffisante : 8 Go de RAM pour la VM. Relancez |
| Dokos ou Forgejo ne répondent pas sur `http://<IP>` ou `http://<IP>:3000` | Votre poste n'est pas sur une plage privée autorisée (`network.lan.allowed_cidrs`), ou la VM est derrière un NAT sans redirection |
| `sudo` refuse le mot de passe dans la VM | Façon A : le mot de passe de votre compte ; façon B : `bin/dokopilot secrets atelier-demo show maintenance_sudo_password` |
| `bin/dokopilot sync` refuse d'envoyer | Lisez le message : fichier modifié non enregistré, conflit, ou configuration Git de la copie modifiée (relancez alors `bin/dokopilot harness atelier-demo`) |
