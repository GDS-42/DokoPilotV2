# Tester sur Hyper-V, depuis WSL, avec Claude Code

Pas à pas complet pour un poste Windows : une VM Debian 13 dans Hyper-V joue le serveur de l'entrepreneur, WSL (Ubuntu) joue le poste de l'intégrateur, et Claude Code vous assiste. Le principe et les variantes sont dans [vm-locale.md](vm-locale.md) ; ce guide en est la version Hyper-V, avec l'installation classique de Debian (variante C).

Comptez une heure et demie, dont 30 à 60 minutes d'installation automatique.

## 0. Ce qu'il faut

- Windows 10 ou 11 **Pro, Entreprise ou Éducation** (Hyper-V n'existe pas en édition Famille), processeur x86-64 avec la virtualisation activée dans le BIOS.
- **16 Go de RAM** sur le poste : 8 Go pour la VM, le reste pour Windows et WSL. 60 Go de disque libre.
- Un accès à Internet par câble de préférence (le commutateur « externe » d'Hyper-V fonctionne mieux sur une carte Ethernet que sur le Wi-Fi).
- Un compte GitHub qui a accès au dépôt privé `GDS-42/DokoPilotV2`.

## 1. Activer Hyper-V et WSL (une fois)

Dans PowerShell **en administrateur** :

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
wsl --install -d Ubuntu-24.04
```

Redémarrez Windows. Au premier lancement d'« Ubuntu 24.04 », choisissez un nom d'utilisateur en minuscules (par exemple `adrien`) : ce sera aussi votre **compte de maintenance** sur le serveur. Il ne doit pas s'appeler `installation`, `root`, `admin`, `debian` ni `ubuntu`.

## 2. Créer la VM

1. Téléchargez l'ISO **netinst amd64** de Debian 13 depuis <https://www.debian.org/distrib/netinst> (fichier `debian-13.x.y-amd64-netinst.iso`, dossier `current`).
2. Dans PowerShell **en administrateur**, adaptez le chemin de l'ISO puis lancez :

```powershell
$vm  = "dokopilot-demo"
$iso = "$env:USERPROFILE\Downloads\debian-13.x.y-amd64-netinst.iso"   # à adapter
New-Item -ItemType Directory -Force C:\Hyper-V | Out-Null

# Commutateur « externe » : la VM reçoit une adresse de votre box, joignable depuis WSL.
# À créer une seule fois ; la connexion réseau du poste se coupe quelques secondes.
if (-not (Get-VMSwitch -Name "Externe" -ErrorAction SilentlyContinue)) {
  $nic = Get-NetAdapter -Physical | Where-Object Status -eq Up | Select-Object -First 1
  New-VMSwitch -Name "Externe" -NetAdapterName $nic.Name -AllowManagementOS $true
}

New-VM -Name $vm -Generation 2 -MemoryStartupBytes 8GB -SwitchName "Externe" `
  -NewVHDPath "C:\Hyper-V\$vm.vhdx" -NewVHDSizeBytes 60GB
Set-VM -Name $vm -ProcessorCount 4 -StaticMemory -AutomaticCheckpointsEnabled $false
Set-VMFirmware -VMName $vm -SecureBootTemplate MicrosoftUEFICertificateAuthority
Add-VMDvdDrive -VMName $vm -Path $iso
Set-VMFirmware -VMName $vm -FirstBootDevice (Get-VMDvdDrive -VMName $vm)
Start-VM -Name $vm
vmconnect.exe localhost $vm
```

Points importants : mémoire **fixe** (la mémoire dynamique peut affamer la construction de l'image Dokos), modèle de démarrage sécurisé « Autorité de certification UEFI Microsoft » (celui de Windows refuse Debian).

## 3. Installer Debian 13

Dans la fenêtre de la VM, choisissez « Install » et répondez :

| Écran | Réponse |
| --- | --- |
| Langue, pays, clavier | Français, France, Français |
| Nom de machine | `dokopilot-demo` ; domaine : vide |
| Mot de passe du superutilisateur (root) | **vide**, deux fois : le compte créé ensuite reçoit sudo |
| Nom complet, identifiant | `Installation`, `installation`, puis un mot de passe dont vous vous souviendrez |
| Partitionnement | Assisté, utiliser un disque entier, tout dans une seule partition |
| Miroir | France, `deb.debian.org` |
| Logiciels | **décochez** « environnement de bureau » et « GNOME » ; **cochez** « serveur SSH » et « utilitaires usuels du système » |

À l'écran « Terminer l'installation », éjectez l'ISO (menu **Média → Lecteur DVD → Éjecter**) puis continuez. Connectez-vous sur la console avec `installation` et relevez l'adresse IP :

```bash
ip -4 addr show scope global
```

C'est l'adresse `inet 192.168.x.y` (ou `10.…`). Dans PowerShell, faites ensuite un **point de contrôle** pour pouvoir recommencer à cet état :

```powershell
Checkpoint-VM -Name dokopilot-demo -SnapshotName "Debian installée"
```

## 4. Préparer WSL (poste de l'intégrateur)

Dans Ubuntu (WSL) :

```bash
sudo apt update && sudo apt install -y git make python3-venv age openssh-client gh bubblewrap socat
ssh-keygen -t ed25519                         # Entrée à chaque question
ssh-copy-id installation@<IP>                 # mot de passe du compte installation
ssh installation@<IP> 'sudo -v && echo OK'    # vérifie SSH et sudo
gh auth login                                 # GitHub.com, HTTPS, connexion par le navigateur
gh repo clone GDS-42/DokoPilotV2 ~/DokoPilotV2 -- -b feat/installation-v0
cd ~/DokoPilotV2
make setup
bin/dokopilot doctor
```

Clonez dans votre dossier Linux (`~`), jamais sous `/mnt/c` : les droits des clés SSH et age y sont faux et tout y est lent. Tant que la pull request n'est pas fusionnée, le travail est sur la branche `feat/installation-v0`.

Si `ssh-copy-id` n'aboutit pas, vérifiez d'abord depuis PowerShell que Windows joint la VM : `Test-NetConnection <IP> -Port 22`.

## 5. Installer DokoPilot

Toujours dans `~/DokoPilotV2` :

```bash
bin/dokopilot init demo --address <IP> --maintenance-user "$USER" --bootstrap-user installation
bin/dokopilot config demo            # affiche la configuration effective, sans rien changer
bin/dokopilot bootstrap demo -K      # demande le mot de passe sudo du compte installation
bin/dokopilot install demo           # 30 à 60 minutes ; se relance sans risque en cas d'erreur
bin/dokopilot check demo
```

- `init` crée votre clé age (`~/.config/dokopilot/age-identity.txt`, à sauvegarder) et les secrets chiffrés.
- `bootstrap` crée votre compte de maintenance (clé SSH, sudo avec mot de passe) et ferme SSH à tout autre compte. Lancez-le **vous-même** dans un terminal : il pose une question.
- `install` neutralise ensuite le compte `installation` (plus de connexion ni de sudo) : c'est voulu. Pour vous connecter au serveur : `bin/dokopilot ssh demo` ; mot de passe sudo : `bin/dokopilot secrets demo show maintenance_sudo_password`.
- Faites un point de contrôle Hyper-V « DokoPilot installé » à la fin.

### Avec Claude Code

Installez Claude Code dans WSL (voir <https://code.claude.com/docs/en/setup>, version 2.1.295 ou plus récente), puis lancez-le dans le dépôt : `cd ~/DokoPilotV2 && claude`. Il lit `CLAUDE.md` et ce guide. Les commandes qui posent une question (`ssh-copy-id`, `gh auth login`, `bootstrap -K`) restent à lancer vous-même dans un autre terminal. Exemple de demande :

```text
Je teste DokoPilot V2 sur une VM Hyper-V Debian 13 d'adresse <IP>, en suivant docs/vm-hyperv.md.
Le bootstrap est fait. Lance `bin/dokopilot config demo`, puis `bin/dokopilot install demo` en
arrière-plan et suis-le jusqu'au bout, puis `bin/dokopilot check demo`. En cas d'échec, lis le
journal, explique-moi la cause et propose une correction ; demande-moi avant de modifier un fichier
du dépôt ou de lancer une commande sur la VM en dehors de bin/dokopilot. N'affiche jamais un secret,
ne pousse rien, ne fusionne rien.
```

## 6. Tester Dokos dans le navigateur

Depuis Windows, ouvrez `http://<IP>` et connectez-vous en entrepreneur : `camille.martin@atelier-demo.test`, mot de passe donné par `bin/dokopilot secrets demo show entrepreneur_password` (à lancer vous-même, pas par Claude Code). À voir :

- **eTransactions › Factures fournisseurs** : les cinq factures électroniques reçues, à traiter ;
- les **factures de vente** : trois sont en retard de paiement ;
- les **transactions bancaires** du compte « Banque Démo », à rapprocher.

[donnees-de-test.md](donnees-de-test.md) décrit toutes les données.

## 7. Tester l'agent de l'entrepreneur

```bash
cd ~/DokoPilotV2 && bin/dokopilot harness demo     # crée ~/DokoPilot/demo et le jeton de l'assistant
cd ~/DokoPilot/demo && claude                      # session distincte, comme l'entrepreneur
```

Au premier lancement, acceptez la confiance dans le dossier et le serveur MCP `dokos`. Essayez :

1. « Quelles factures fournisseurs reçues sont à traiter ? »
2. « Traite les factures fournisseurs reçues » : l'agent prépare la facture d'achat conforme en brouillon et signale l'écart de prix (Panneaux Démo Bois) et la réception incomplète (Quincaillerie Démo Pro).
3. « Prépare les relances des impayés » : sans messagerie configurée, il donne le texte des relances à copier ; rien n'est envoyé.
4. « Propose le rapprochement bancaire ».
5. « Valide la facture fournisseur … » : l'agent doit refuser ; Dokos refuserait de toute façon.

Vérifiez dans le navigateur que les brouillons sont apparus, puis validez-en un **vous-même** : c'est le geste réservé à l'humain. `claude --agent atelier` ouvre le contexte technique (shell, sans messagerie).

N'utilisez pas la session du dépôt (`~/DokoPilotV2`) pour ce test : l'agent de l'entrepreneur ne doit voir que son dossier.

## 8. Recommencer, sauvegarder, nettoyer

| Besoin | Commande |
| --- | --- |
| Recharger les données de test | `bin/dokopilot demo-reset demo` |
| Tester sauvegarde et restauration | `bin/dokopilot backup demo`, puis `bin/dokopilot restore demo --confirm` |
| Revenir à Debian tout juste installé | PowerShell : `Restore-VMCheckpoint -VMName dokopilot-demo -Name "Debian installée" -Confirm:$false`, puis dans WSL `ssh-keygen -R <IP>` et reprendre à l'étape 4 (`ssh-copy-id`) |
| La VM a changé d'adresse | `bin/dokopilot init demo --address <nouvelle IP>`, puis `bin/dokopilot harness demo` |
| Tout supprimer | PowerShell : `Stop-VM dokopilot-demo -TurnOff; Remove-VM dokopilot-demo -Force; Remove-Item C:\Hyper-V\dokopilot-demo.vhdx` |

## En cas de problème

| Symptôme | Piste |
| --- | --- |
| La VM ne démarre pas sur l'ISO (« No operating system ») | Démarrage sécurisé : modèle « Autorité de certification UEFI Microsoft » (étape 2) ou désactivez-le dans les paramètres de la VM |
| Pas d'adresse IP dans la VM | Le commutateur « Externe » est lié à la mauvaise carte, ou le Wi-Fi refuse le pont : liez-le à la carte Ethernet |
| Windows joint la VM, WSL non | Testez `ping <IP>` dans WSL ; sous Windows 11, le mode réseau « mirrored » de WSL (`networkingMode=mirrored` dans `%UserProfile%\.wslconfig`, puis `wsl --shutdown`) est une piste |
| `bootstrap` : « Missing sudo password » ou refus de sudo | Ajoutez `-K` et donnez le mot de passe du compte `installation` |
| `install` s'arrête sur « Debian 13 » ou « x86_64 » | La VM n'est pas une Debian 13 amd64 |
| `toomanyrequests` pendant l'installation | Docker Hub limite les téléchargements anonymes : patientez quelques minutes, puis relancez `bin/dokopilot install demo` |
| Construction de l'image interrompue | Mémoire : 8 Go **fixes** pour la VM, puis relancez |
| Dokos ne répond pas sur `http://<IP>` | `bin/dokopilot check demo` ; l'adresse de votre poste doit être dans une plage privée (`network.lan.allowed_cidrs`) |
| Claude Code refuse la configuration du dossier `~/DokoPilot/demo` | Mettez Claude Code à jour (2.1.295 ou plus) ; installez `bubblewrap` et `socat` pour son bac à sable |
