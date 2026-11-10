#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Builds a cloud-init NoCloud seed image (ISO labelled "cidata") for a Debian 13 cloud image.
# Works with any hypervisor that can attach an ISO: libvirt/QEMU, VirtualBox, VMware, UTM, Hyper-V, Proxmox.
#
#   vm/make-seed.sh --user <maintenance account> [--key ~/.ssh/id_ed25519.pub] [--hostname dokopilot-demo] [--out seed.iso]
set -euo pipefail

user="${USER:-integrateur}"
key="$HOME/.ssh/id_ed25519.pub"
hostname="dokopilot-demo"
out="seed.iso"

while (( $# > 0 )); do
  case "$1" in
    --user) user="$2"; shift 2 ;;
    --key) key="$2"; shift 2 ;;
    --hostname) hostname="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    -h|--help) sed -n '3,6p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done

if [[ ! "$user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "Nom de compte invalide : $user (minuscules, chiffres, tirets)" >&2; exit 2
fi
if [[ ! -r "$key" ]]; then
  echo "Clé SSH publique introuvable : $key (créez-la avec ssh-keygen -t ed25519)" >&2; exit 2
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
ssh_key="$(head -n 1 "$key")"
stamp="$(date +%s)"

render() {
  # Literal replacement (no sed): the key may contain characters special to sed.
  python3 - "$1" "$2" "$user" "$ssh_key" "$hostname" "$stamp" <<'PY'
import sys
src, dst, user, key, host, stamp = sys.argv[1:]
text = open(src, encoding="utf-8").read()
for marker, value in (("@USER@", user), ("@SSH_KEY@", key), ("@HOSTNAME@", host), ("@STAMP@", stamp)):
    text = text.replace(marker, value)
open(dst, "w", encoding="utf-8").write(text)
PY
}
render "$here/cloud-init/user-data.tmpl" "$work/user-data"
render "$here/cloud-init/meta-data.tmpl" "$work/meta-data"

if command -v cloud-localds >/dev/null; then
  cloud-localds "$out" "$work/user-data" "$work/meta-data"
elif command -v xorrisofs >/dev/null; then
  xorrisofs -quiet -output "$out" -volid cidata -joliet -rock "$work/user-data" "$work/meta-data"
elif command -v genisoimage >/dev/null; then
  genisoimage -quiet -output "$out" -volid cidata -joliet -rock "$work/user-data" "$work/meta-data"
elif command -v mkisofs >/dev/null; then
  mkisofs -quiet -output "$out" -volid cidata -joliet -rock "$work/user-data" "$work/meta-data"
elif command -v hdiutil >/dev/null; then
  mkdir "$work/cidata" && cp "$work/user-data" "$work/meta-data" "$work/cidata/"
  hdiutil makehybrid -quiet -o "$out" -iso -joliet -default-volume-name cidata "$work/cidata"
else
  echo "Aucun outil ISO trouvé : installez cloud-image-utils, xorriso ou genisoimage (macOS : hdiutil est fourni)." >&2
  exit 1
fi
echo "Image d'amorçage créée : $out (compte $user, machine $hostname)"
