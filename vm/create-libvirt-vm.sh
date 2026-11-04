#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Creates the local demo VM with libvirt/QEMU on a Linux workstation:
# official Debian 13 "generic" cloud image (checksum verified), 4 vCPU, 8 GB RAM, 40 GB disk,
# cloud-init seed with your maintenance account and SSH key.
#
#   vm/create-libvirt-vm.sh [--name dokopilot-demo] [--user <account>] [--key ~/.ssh/id_ed25519.pub]
#                           [--memory 8192] [--vcpus 4] [--disk 40] [--network default]
set -euo pipefail

name="dokopilot-demo"
user="${USER:-integrateur}"
key="$HOME/.ssh/id_ed25519.pub"
memory=8192
vcpus=4
disk=40
network="default"
images="${DOKOPILOT_VM_DIR:-$HOME/.local/share/dokopilot/vm}"

while (( $# > 0 )); do
  case "$1" in
    --name) name="$2"; shift 2 ;;
    --user) user="$2"; shift 2 ;;
    --key) key="$2"; shift 2 ;;
    --memory) memory="$2"; shift 2 ;;
    --vcpus) vcpus="$2"; shift 2 ;;
    --disk) disk="$2"; shift 2 ;;
    --network) network="$2"; shift 2 ;;
    -h|--help) sed -n '3,9p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done

for cmd in virsh virt-install qemu-img curl sha512sum; do
  command -v "$cmd" >/dev/null || { echo "Commande absente : $cmd (paquets libvirt-daemon-system, virtinst, qemu-utils, curl)" >&2; exit 1; }
done

case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) echo "Architecture non prise en charge : $(uname -m)" >&2; exit 1 ;;
esac

base_url="https://cloud.debian.org/images/cloud/trixie/latest"
image="debian-13-generic-${arch}.qcow2"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$images"

if virsh -c qemu:///system dominfo "$name" >/dev/null 2>&1; then
  echo "La VM $name existe déjà. Supprimez-la d'abord : virsh -c qemu:///system destroy $name; virsh -c qemu:///system undefine $name --remove-all-storage" >&2
  exit 1
fi

echo "Téléchargement et vérification de l'image Debian 13 ($arch)…"
curl -fsSL -o "$images/SHA512SUMS" "$base_url/SHA512SUMS"
if [[ ! -f "$images/$image" ]] || ! (cd "$images" && grep " $image\$" SHA512SUMS | sha512sum -c --status); then
  curl -fL --progress-bar -o "$images/$image" "$base_url/$image"
fi
(cd "$images" && grep " $image\$" SHA512SUMS | sha512sum -c --quiet) || { echo "Somme de contrôle invalide pour $image" >&2; exit 1; }

disk_path="$images/$name.qcow2"
seed_path="$images/$name-seed.iso"
qemu-img create -q -f qcow2 -F qcow2 -b "$images/$image" "$disk_path" "${disk}G"
"$here/make-seed.sh" --user "$user" --key "$key" --hostname "$name" --out "$seed_path"

os_variant="debian13"
if ! virt-install --osinfo list 2>/dev/null | grep -q '^debian13'; then
  os_variant="debiantesting"
fi

virt-install --connect qemu:///system \
  --name "$name" --memory "$memory" --vcpus "$vcpus" \
  --disk "path=$disk_path,format=qcow2,bus=virtio" \
  --disk "path=$seed_path,device=cdrom" \
  --osinfo "$os_variant" --network "network=$network,model=virtio" \
  --graphics none --import --noautoconsole

echo "Attente de l'adresse IP de la VM…"
ip=""
for _ in $(seq 1 60); do
  ip="$(virsh -c qemu:///system domifaddr "$name" --source lease 2>/dev/null | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}')"
  [[ -n "$ip" ]] && break
  sleep 5
done
if [[ -z "$ip" ]]; then
  echo "Adresse IP introuvable ; consultez : virsh -c qemu:///system domifaddr $name" >&2
  exit 1
fi

cat <<MSG

VM $name prête à l'adresse $ip (compte de maintenance : $user).
Étapes suivantes :
  bin/dokopilot init demo --address $ip --maintenance-user $user
  bin/dokopilot install demo
MSG
