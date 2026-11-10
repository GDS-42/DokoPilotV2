# Tester sur Hyper-V, depuis WSL, avec Claude Code

Pas à pas complet pour un poste Windows : une VM Debian 13 dans Hyper-V joue le serveur de
l'entrepreneur, et vous installez DokoPilot avec une seule commande, de l'une de ces deux façons :

- **A. Tout sur la VM** : la commande se lance dans la VM, qui est à la fois le serveur et le poste
  où tourne l'agent ;
- **B. La VM comme serveur distant** : la commande se lance dans WSL (Ubuntu), qui joue le poste
  et pilote la VM en SSH ; l'agent tourne dans WSL.

Le principe et les autres hyperviseurs sont dans [vm-locale.md](vm-locale.md) ; ce guide en est la
version Hyper-V, avec l'installation classique de Debian. Comptez une heure et demie, dont 30 à 60
minutes d'installation automatique.

## 0. Ce qu'il faut

- Windows 10 ou 11 **Pro, Entreprise ou Éducation** (Hyper-V n'existe pas en édition Famille),
  processeur x86-64 avec la virtualisation activée dans le BIOS.
- **16 Go de RAM** sur le poste : 8 Go pour la VM, le reste pour Windows et WSL. 60 Go de disque
  libre.
- Un accès à Internet par câble de préférence (le commutateur « externe » d'Hyper-V fonctionne
  mieux sur une carte Ethernet que sur le Wi-Fi).

## 1. Activer Hyper-V et WSL (une fois)

Dans PowerShell **en administrateur** :

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
wsl --install -d Ubuntu-24.04
```

Redémarrez Windows. Au premier lancement d'« Ubuntu 24.04 », choisissez un nom d'utilisateur en
minuscules (par exemple `adrien`) : pour la façon B, ce sera aussi votre **compte de maintenance**
sur le serveur. Il ne doit pas s'appeler `root`, `admin`, `debian`, `ubuntu`, `frappe` ni
`installation`.
WSL sert aussi, dans la façon A, à vous connecter à la VM en SSH.

## 2. Créer la VM

1. Téléchargez l'ISO **netinst amd64** de Debian 13 depuis <https://www.debian.org/distrib/netinst>
   (fichier `debian-13.x.y-amd64-netinst.iso`, dossier `current`).
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

Points importants : mémoire **fixe** de 8 Go (la mémoire dynamique peut affamer la construction de
l'image Dokos, et la façon A demande au moins 7,5 Gio visibles dans la VM), modèle de démarrage
sécurisé « Autorité de certification UEFI Microsoft » (celui de Windows refuse Debian).

## 3. Installer Debian 13

Dans la fenêtre de la VM, choisissez « Install » et répondez :

| Écran | Réponse |
| --- | --- |
| Langue, pays, clavier | Français, France, Français |
| Nom de machine | `dokopilot-demo` ; domaine : vide |
| Mot de passe du superutilisateur (root) | **vide**, deux fois : le compte créé ensuite reçoit sudo |
| Nom complet, identifiant | Façon A : **vous** (par exemple `Adrien Martin`, `adrien`), car ce compte deviendra le compte de maintenance ; façon B : `Installation`, `installation`, car ce compte ne sert qu'au premier passage. Puis un mot de passe dont vous vous souviendrez |
| Partitionnement | Assisté, utiliser un disque entier, tout dans une seule partition |
| Miroir | France, `deb.debian.org` |
| Logiciels | **décochez** « environnement de bureau » et « GNOME » ; **cochez** « serveur SSH » et « utilitaires usuels du système » |

À l'écran « Terminer l'installation », éjectez l'ISO (menu **Média → Lecteur DVD → Éjecter**) puis
continuez. Connectez-vous sur la console avec ce compte et relevez l'adresse IP :

```bash
ip -4 addr show scope global
```

C'est l'adresse `inet 192.168.x.y` (ou `10.…`). Dans PowerShell, faites ensuite un **point de
contrôle** pour pouvoir recommencer à cet état :

```powershell
Checkpoint-VM -Name dokopilot-demo -SnapshotName "Debian installée"
```

Dans Ubuntu (WSL), préparez votre clé SSH et vérifiez que la VM répond :

```bash
sudo apt update && sudo apt install -y curl openssh-client
ssh-keygen -t ed25519                         # Entrée à chaque question, si vous n'avez pas de clé
ssh-copy-id <compte>@<IP>                     # façon A : votre compte ; façon B : installation
ssh -t <compte>@<IP> 'sudo -v && echo OK'     # vérifie SSH et sudo (mot de passe du compte)
```

Si `ssh-copy-id` n'aboutit pas, vérifiez d'abord depuis PowerShell que Windows joint la VM :
`Test-NetConnection <IP> -Port 22`.

## 4. Façon A : tout sur la VM, en une commande

Connectez-vous à la VM depuis WSL (`ssh <compte>@<IP>`) ; votre clé, copiée à l'étape 3, reste
autorisée après l'installation, qui n'accepte plus que les clés. Puis, dans la VM :

```bash
command -v curl || sudo apt install -y curl
curl -fsSL https://raw.githubusercontent.com/GDS-42/DokoPilotV2/v0.2.0/install.sh | sh
```

Le script vérifie la VM, installe ce qui manque, récupère DokoPilot dans `~/DokoPilotV2`, puis
l'assistant pose ses questions ([installation.md](installation.md) les détaille). Répondez :

| Question | Réponse |
| --- | --- |
| Où installer DokoPilot ? | `local` |
| Démo ou production ? | `demo` |
| Identifiant court | Entrée (`atelier-demo`) |
| Quel agent ? | `opencode`, `claude-code` ou `les-deux` |
| Modèle d'IA | Selon l'agent : votre fournisseur, Entrée pour les modèles proposés, la clé d'API (saisie masquée) ou l'abonnement Claude |
| Entreprise, dirigeant | Entrée à chaque question (Atelier Démo, Camille Martin) ; « Ajouter une autre personne ? » : Entrée (non) |
| Réseau | Entrée (`lan`) ; messagerie, agenda, import : Entrée (non) |

À chaque invite `BECOME password:` d'Ansible (installation, harnais, contrôles), tapez le mot de
passe de **votre compte dans la VM** : il ne change pas. L'agent est configuré dans la VM, dans
`~/DokoPilot/atelier-demo`.

## 5. Façon B : la VM pilotée depuis WSL

Dans Ubuntu (WSL) :

```bash
curl -fsSL https://raw.githubusercontent.com/GDS-42/DokoPilotV2/v0.2.0/install.sh | sh
```

Gardez DokoPilot dans votre dossier Linux (`~/DokoPilotV2`, le défaut), jamais sous `/mnt/c` :
les droits des clés SSH et age y sont faux et tout y est lent. Répondez :

| Question | Réponse |
| --- | --- |
| Où installer DokoPilot ? | Entrée (`distant`) |
| Adresse du serveur, port SSH | `<IP>`, Entrée (22) |
| Compte initial du serveur | `installation` |
| Votre compte de maintenance, votre clé SSH publique | Entrée (votre compte WSL, `~/.ssh/id_ed25519.pub`) |
| Démo ou production, identifiant, agent, modèles | comme pour la façon A |
| Entreprise, dirigeant, réseau | comme pour la façon A |

Au premier passage (`bootstrap`), l'invite `BECOME password:` attend le mot de passe du compte
`installation`. Ce passage crée votre compte de maintenance (clé SSH, sudo avec mot de passe) et
ferme SSH à tout autre compte ; l'installation neutralise ensuite le compte `installation` (plus
de connexion ni de sudo) : c'est voulu. Pour vous connecter au serveur : `bin/dokopilot ssh
atelier-demo` depuis `~/DokoPilotV2` ; mot de passe sudo : `bin/dokopilot secrets atelier-demo
show maintenance_sudo_password`. L'agent est configuré dans WSL, dans `~/DokoPilot/atelier-demo`.

### Avec Claude Code

L'assistant d'installation pose des questions et Ansible demande des mots de passe : lancez-le
vous-même dans un terminal. Claude Code peut ensuite vous aider dans WSL, par exemple si une étape
échoue. Installez-le (voir <https://code.claude.com/docs/en/setup>, version 2.1.295 ou plus
récente), puis lancez-le dans le dépôt : `cd ~/DokoPilotV2 && claude`. Il lit `CLAUDE.md` et ce
guide. Exemple de demande :

```text
Je teste DokoPilot V2 sur une VM Hyper-V Debian 13 d'adresse <IP>, en suivant docs/vm-hyperv.md,
façon B. L'assistant d'installation s'est arrêté à l'étape « install ». Lis la sortie que je te
colle, explique-moi la cause et propose une correction. Tu peux relancer
`bin/dokopilot install atelier-demo` en arrière-plan et le suivre, puis
`bin/dokopilot check atelier-demo`. Demande-moi avant de modifier un fichier du dépôt ou de lancer
une commande sur la VM en dehors de bin/dokopilot. N'affiche jamais un secret, ne pousse rien, ne
fusionne rien.
```

En façon B, `install` et `check` n'ont pas besoin de mot de passe après le premier passage
(`bin/dokopilot` le tire des secrets) ; en façon A, chaque commande demande le vôtre (`-K`) :
lancez-les vous-même. Une fois la cause corrigée, relancez l'assistant : il reprend où il s'était
arrêté.

## 6. Vérifier

Les commandes `bin/dokopilot` se lancent depuis `~/DokoPilotV2` : dans la VM (façon A) ou dans
WSL (façon B).

- **Contrôles** : façon A, `bin/dokopilot check atelier-demo --local -K` ; façon B,
  `bin/dokopilot check atelier-demo`. La commande échoue au moindre écart.
- **Dokos** : depuis Windows, ouvrez `http://<IP>`.
- **Forgejo** : depuis Windows, ouvrez `http://<IP>:3000`. Compte `camille`, mot de passe initial
  dans `~/.config/dokopilot/atelier-demo/forgejo-password` (sur la machine de l'agent : la VM en
  façon A, WSL en façon B) ; changez-le. L'organisation `atelier-demo` contient `entreprise`,
  `direction` et `perso-camille`.
- **L'espace de travail** : `ls ~/DokoPilot/atelier-demo` montre `AGENTS.md`, les dossiers
  `00-Boite-de-reception` à `90-Modeles`, `direction/` et `perso/`.
- **L'envoi vers Forgejo** :

  ```bash
  cd ~/DokoPilot/atelier-demo
  echo "# Essai de synchronisation" > 00-Boite-de-reception/essai.md
  git add 00-Boite-de-reception/essai.md && git commit -m "Ajoute une note d'essai"
  ~/DokoPilotV2/bin/dokopilot sync atelier-demo
  ```

  `sync` montre l'enregistrement à envoyer (`essai.md`) et demande votre accord : tapez `o`.
  Il affiche ensuite `✓ entreprise : 1 envoyé(s)`, puis `déjà à jour` pour `direction` et
  `perso` ; la note apparaît dans Forgejo, dans `atelier-demo/entreprise`.

Faites un point de contrôle Hyper-V « DokoPilot installé ».

## 7. Tester Dokos dans le navigateur

Depuis Windows, ouvrez `http://<IP>` et connectez-vous en entrepreneur :
`camille.martin@atelier-demo.test`, mot de passe donné par
`bin/dokopilot secrets atelier-demo show entrepreneur_password` (à lancer vous-même, pas par
Claude Code). À voir :

- **eTransactions › Factures fournisseurs** : les cinq factures électroniques reçues, à traiter ;
- les **factures de vente** : trois sont en retard de paiement ;
- les **transactions bancaires** du compte « Banque Démo », à rapprocher.

[donnees-de-test.md](donnees-de-test.md) décrit toutes les données.

## 8. Tester l'agent de l'entrepreneur

Installez d'abord l'agent choisi sur la machine de l'agent (la VM ou WSL) : le résumé de fin
d'installation liste ce qui manque, et [poste-entrepreneur.md](poste-entrepreneur.md) donne les
commandes (« 1. Préparer l'ordinateur »). Puis :

```bash
cd ~/DokoPilot/atelier-demo && claude              # ou : opencode
```

Au premier lancement de Claude Code, acceptez la confiance dans le dossier et le serveur MCP
`dokos`. Essayez :

1. « Quelles factures fournisseurs reçues sont à traiter ? »
2. « Traite les factures fournisseurs reçues » : l'agent prépare la facture d'achat conforme en
   brouillon et signale l'écart de prix (Panneaux Démo Bois) et la réception incomplète
   (Quincaillerie Démo Pro).
3. « Prépare les relances des impayés » : sans messagerie configurée, il donne le texte des
   relances à copier ; rien n'est envoyé.
4. « Propose le rapprochement bancaire ».
5. « Valide la facture fournisseur … » : l'agent doit refuser ; Dokos refuserait de toute façon.
6. « Note dans la boîte de réception ce que tu as trouvé, puis envoie-le sur Forgejo » : il écrit
   la note et propose de la commiter, mais refuse `git push` ; envoyez-la vous-même avec
   `bin/dokopilot sync atelier-demo`.

Vérifiez dans le navigateur que les brouillons sont apparus, puis validez-en un **vous-même** :
c'est le geste réservé à l'humain. `claude --agent atelier` ouvre le contexte technique (shell,
sans messagerie).

N'utilisez pas la session du dépôt (`~/DokoPilotV2`) pour ce test : l'agent de l'entrepreneur ne
doit voir que son dossier.

## 9. Recommencer, sauvegarder, nettoyer

En façon A, ajoutez `--local -K` aux commandes `demo-reset`, `backup` et `restore`.

| Besoin | Commande |
| --- | --- |
| Recharger les données de test | `bin/dokopilot demo-reset atelier-demo` |
| Tester sauvegarde et restauration | `bin/dokopilot backup atelier-demo`, puis `bin/dokopilot restore atelier-demo --confirm` |
| Revenir à Debian tout juste installé | PowerShell : `Restore-VMCheckpoint -VMName dokopilot-demo -Name "Debian installée" -Confirm:$false`, puis reprenez à l'étape 3 (`ssh-copy-id`). Façon B : déplacez d'abord l'ancien espace de travail de WSL (`mv ~/DokoPilot/atelier-demo ~/DokoPilot/atelier-demo.ancien`) ; à la relance, refusez la reprise si elle est proposée et acceptez de remplacer l'installation « atelier-demo » |
| La VM a changé d'adresse (façon B) | `bin/dokopilot init atelier-demo --address <nouvelle IP>`, puis `bin/dokopilot install atelier-demo` (adresse de Forgejo) et `bin/dokopilot harness atelier-demo` (adresse des copies) |
| Tout supprimer | PowerShell : `Stop-VM dokopilot-demo -TurnOff; Remove-VM dokopilot-demo -Force; Remove-Item C:\Hyper-V\dokopilot-demo.vhdx` |

## En cas de problème

| Symptôme | Piste |
| --- | --- |
| La VM ne démarre pas sur l'ISO (« No operating system ») | Démarrage sécurisé : modèle « Autorité de certification UEFI Microsoft » (étape 2) ou désactivez-le dans les paramètres de la VM |
| Pas d'adresse IP dans la VM | Le commutateur « Externe » est lié à la mauvaise carte, ou le Wi-Fi refuse le pont : liez-le à la carte Ethernet |
| Windows joint la VM, WSL non | Testez `ping <IP>` dans WSL ; sous Windows 11, le mode réseau « mirrored » de WSL (`networkingMode=mirrored` dans `%UserProfile%\.wslconfig`, puis `wsl --shutdown`) est une piste |
| « Tout sur cette machine » impossible ici | L'assistant donne la raison : mémoire (8 Go **fixes**), disque, système, compte root ou non nominatif, sites injoignables |
| `bootstrap` : refus de sudo | À l'invite `BECOME password:`, donnez le mot de passe du compte `installation` |
| `install` s'arrête sur « Debian 13 » ou « x86_64 » | La VM n'est pas une Debian 13 amd64 |
| `toomanyrequests` pendant l'installation | Docker Hub limite les téléchargements anonymes : patientez quelques minutes, puis relancez l'assistant |
| Construction de l'image interrompue | Mémoire : 8 Go **fixes** pour la VM, puis relancez |
| Dokos ou Forgejo ne répondent pas sur `http://<IP>` ou `http://<IP>:3000` | Lancez les contrôles ; l'adresse de votre poste doit être dans une plage privée (`network.lan.allowed_cidrs`) |
| Plus de connexion SSH à la VM après la façon A | Seules les clés de `~/.ssh/authorized_keys` (copiées avant l'installation) ouvrent une session : passez par la console Hyper-V, ajoutez votre clé dans `client.local.yml` (`maintenance.users`), puis relancez `bin/dokopilot install atelier-demo --local -K` |
| Claude Code refuse la configuration du dossier `~/DokoPilot/atelier-demo` | Mettez Claude Code à jour (2.1.295 ou plus) ; installez `bubblewrap` et `socat` pour son bac à sable |
