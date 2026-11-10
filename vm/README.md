# VM locale de test

Deux scripts pour créer une VM Debian 13 prête pour `bin/dokopilot install demo` :

- `create-libvirt-vm.sh` : poste Linux avec libvirt/QEMU, tout est automatique ;
- `make-seed.sh` : produit l'image d'amorçage cloud-init (`seed.iso`) à joindre, avec l'image
  Debian 13 « generic », dans VirtualBox, VMware, UTM (Mac), Hyper-V ou Proxmox.

Le mode d'emploi complet est dans [docs/vm-locale.md](../docs/vm-locale.md).
