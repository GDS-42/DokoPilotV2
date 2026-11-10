#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Fast test of the backup and restore scripts (ansible/roles/backup/templates), without
# Docker or root: the scripts are rendered by Ansible into a temporary directory and run
# against a REAL restic repository, with a fake `docker compose` (tests/backup/fake) that
# stands for the Dokos site and the Forgejo volume. Proves:
#   - the snapshot holds the site, the context and no .env, and carries the tags of the
#     site and of the installation;
#   - a lock left by a killed backup does not block the next one;
#   - two installations on one repository (a test or replacement server with the same
#     settings) never prune each other's snapshots;
#   - `latest` is this installation's last snapshot, the previous installation's on a new
#     server, and is refused when several installations could be meant;
#   - a restore that fails half-way leaves Dokos closed and names the way back;
#   - the Forgejo restore replaces the volume from the archive, checked before deleting;
#   - a failed Forgejo copy still sends the Dokos backup, and the run ends in error.
# Needs restic (skipped without it) and the Ansible of .venv. The real stack is covered by
# tests/stack/run.sh and tests/context/run.sh.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
if ! command -v restic > /dev/null; then
  echo "restic absent : test des scripts de sauvegarde ignoré (apt install restic)."
  exit 0
fi

root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
failures=0
ok() { echo "ok    $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
check() { # check <description> <command...>
  local description="$1"
  shift
  if "$@"; then ok "$description"; else fail "$description"; fi
}
equals() { [[ "$1" == "$2" ]] || { echo "      got « $1 », expected « $2 »"; return 1; }; }
contains() { grep -Fq -- "$2" <<< "$1" || { printf '      « %s » not found in:\n%s\n' "$2" "$1"; return 1; }; }
lacks() { ! grep -Fq -- "$2" <<< "$1" || { printf '      « %s » found in:\n%s\n' "$2" "$1"; return 1; }; }

mkdir -p "$root"/{stack,forgejo,etc,bench-backups,forgejo-backups,sbin}
touch "$root/stack/compose.yaml" "$root/forgejo/compose.yaml"
printf "DB_ROOT_PASSWORD='racine-de-test'\n" > "$root/stack/.env"
printf "SECRET_KEY='ne-doit-pas-partir'\n" > "$root/forgejo/.env"
printf "RESTIC_REPOSITORY='%s'\nRESTIC_PASSWORD='mot-de-passe-de-test'\n" "$root/repo" > "$root/etc/restic.env"

(cd "$repo/ansible" && ANSIBLE_CONFIG=ansible.cfg ANSIBLE_NOCOLOR=1 "$repo/.venv/bin/ansible-playbook" -i localhost, \
  -e "test_root=$root" -e "dokopilot_stack_dir=$root/stack" -e "dokopilot_etc_dir=$root/etc" \
  -e "dokopilot_bench_backup_dir=$root/bench-backups" -e "dokopilot_forgejo_dir=$root/forgejo" \
  -e "dokopilot_forgejo_backup_dir=$root/forgejo-backups" -e "backup_script=$root/sbin/dokopilot-backup" \
  -e "backup_lock_file=$root/lock" "$here/render.yml") > "$root/render.log" 2>&1 || { cat "$root/render.log"; exit 1; }
bash -n "$root/sbin/dokopilot-backup" && bash -n "$root/sbin/dokopilot-restore"
ok "the scripts are rendered and parse"

export PATH="$here/fake:$PATH" RESTIC_CACHE_DIR="$root/cache" FAKE_HOST_DIR="$root/bench-backups"
restic_cmd() { RESTIC_REPOSITORY="$root/repo" RESTIC_PASSWORD=mot-de-passe-de-test restic "$@"; }
restic_cmd init --quiet

# use <installation>: the scripts then run as that installation (its identifier, its site
# and its Forgejo volume); "new" is a server that has made no backup yet.
use() {
  export FAKE="$root/state-$1"
  mkdir -p "$FAKE/forgejo-data"
  [[ -f "$FAKE/site-state" ]] || echo "site vide de $1" > "$FAKE/site-state"
  rm -f "$root/etc/install-id"
  if [[ -f "$FAKE/install-id" ]]; then cp "$FAKE/install-id" "$root/etc/install-id"; fi
}
keep_id() { cp "$root/etc/install-id" "$FAKE/install-id"; }
backup() { "$root/sbin/dokopilot-backup" > "$root/out.log" 2>&1; }
restore() { "$root/sbin/dokopilot-restore" "$@" > "$root/out.log" 2>&1; }
snapshots() { restic_cmd snapshots --json "$@" | python3 -c 'import json, sys; print(len(json.load(sys.stdin)))'; }
out() { cat "$root/out.log"; }

# ----------------------------------------------------------------- 1. a backup
use production
echo "comptabilité de la production" > "$FAKE/site-state"
echo "dépôts de la production" > "$FAKE/forgejo-data/depots.txt"
check "backup of the production" backup
keep_id
prod_tag="install:$(cat "$root/etc/install-id")"
check "the installation identifier is private" equals "$(stat -c %a "$root/etc/install-id")" 600
check "one snapshot with the tags dokopilot, site and installation" equals \
  "$(snapshots --tag "dokopilot,site:test.dokopilot.internal,$prod_tag")" 1
listing="$(restic_cmd ls latest)"
check "the snapshot holds the database" contains "$listing" "-database.sql.gz"
check "the snapshot holds the copy of Forgejo" contains "$listing" "/forgejo-backups/current/forgejo-data.tar"
check "no .env in the snapshot" lacks "$listing" "/.env"
check "the working copies are removed" equals "$(find "$root/bench-backups" "$root/forgejo-backups" -name current | wc -l)" 0

# ----------------------------------------------------------------- 2. a stale lock
# A backup that never ends (its input stays open), killed without a chance to remove its lock.
mkfifo "$root/fifo"
RESTIC_REPOSITORY="$root/repo" RESTIC_PASSWORD=mot-de-passe-de-test restic backup --quiet --stdin --stdin-filename x \
  < "$root/fifo" > /dev/null 2>&1 &
slow=$!
exec 8> "$root/fifo"
for _ in $(seq 1 100); do
  [[ -n "$(restic_cmd list locks --no-lock 2> /dev/null)" ]] && break
  sleep 0.1
done
kill -KILL "$slow"
wait "$slow" 2> /dev/null || true
exec 8>&-
if [[ -n "$(restic_cmd list locks --no-lock 2> /dev/null)" ]]; then
  check "a lock left by a killed backup does not block the next one" backup
else
  echo "skip  no stale lock could be left with this version of restic"
fi

# ----------------------------------------------------------------- 3. a new server, then a second installation
use new
echo "site vide du nouveau serveur" > "$FAKE/site-state"
check "on a new server, latest is the previous installation's snapshot" restore --confirm
check "  and the restore says so" contains "$(out)" "c’est celui de l’installation précédente"
check "  the production data are restored" equals "$(cat "$FAKE/site-state")" "comptabilité de la production"
check "  Dokos is reopened" contains "$(tail -n 1 "$FAKE/docker.log")" "start queue-short queue-long scheduler frontend websocket"

use essai
check "backup of a test server on the same repository" backup
keep_id
check "a second backup of the test server" backup
check "the test server pruned none of the production's snapshots" equals "$(( $(snapshots --tag "dokopilot,$prod_tag") >= 1 ))" 1

use production
echo "saisie du jour, à écraser" > "$FAKE/site-state"
check "on the production, latest is its own snapshot, not the newer one of the test server" restore --confirm
check "  the production data are restored" equals "$(cat "$FAKE/site-state")" "comptabilité de la production"
check "  the state before the restore is kept" equals "$(snapshots --tag "dokopilot-pre-restore,$prod_tag")" 1
check "  the restore mentions the other installation" contains "$(out)" "d’autres installations"

use another-new
if restore --confirm; then fail "latest must be refused when several installations could be meant"; else
  check "latest is refused when several installations could be meant" contains "$(out)" "plusieurs installations"
fi
check "  nothing was stopped" lacks "$(cat "$FAKE/docker.log" 2> /dev/null || true)" " stop "

# ----------------------------------------------------------------- 4. refusals and failures
use production
if restore; then fail "a restore without --confirm must be refused"; else ok "a restore without --confirm is refused"; fi
echo "état juste avant la restauration ratée" > "$FAKE/site-state"
: > "$FAKE/docker.log"
if FAKE_FAIL=migrate restore --confirm; then fail "a failed migration must fail the restore"; else ok "a failed migration fails the restore"; fi
check "  Dokos stays closed" lacks "$(cat "$FAKE/docker.log")" "start queue-short"
check "  the message says so" contains "$(out)" "Dokos reste fermé aux utilisateurs"
way_back="$(sed -n 's/.*revenir à l.état d.avant la restauration : .*--snapshot \([0-9a-f]*\)$/\1/p' "$root/out.log")"
check "  the message names the snapshot of the state before" test -n "$way_back"
check "the way back restores the state before the failed restore" restore --confirm --no-pre-backup --snapshot "$way_back"
check "  that state is back" equals "$(cat "$FAKE/site-state")" "état juste avant la restauration ratée"
check "  Dokos is reopened" contains "$(tail -n 1 "$FAKE/docker.log")" "start queue-short"
if restore --confirm --snapshot ffffffff; then fail "an unknown snapshot must be refused"; else ok "an unknown snapshot is refused"; fi

# ----------------------------------------------------------------- 5. Forgejo
echo "dépôts abîmés" > "$FAKE/forgejo-data/depots.txt"
echo "fichier en trop" > "$FAKE/forgejo-data/intrus.txt"
first="$(restic_cmd snapshots --json --tag "dokopilot,$prod_tag" | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["short_id"])')"
check "restore of Forgejo from a chosen snapshot" restore --confirm --forgejo --snapshot "$first"
check "  the repositories are those of the snapshot" equals "$(cat "$FAKE/forgejo-data/depots.txt")" "dépôts de la production"
check "  nothing else is left in the volume" equals "$(ls "$FAKE/forgejo-data")" "depots.txt"
check "  the site was not touched" lacks "$(tail -n 4 "$FAKE/docker.log")" "run.py restore"
before="$(snapshots --tag "dokopilot,$prod_tag")"
if FAKE_FAIL=forgejo-copy backup; then fail "a failed copy of Forgejo must end the backup in error"; else ok "a failed copy of Forgejo ends the backup in error"; fi
check "  the message says the backup is incomplete" contains "$(out)" "Sauvegarde INCOMPLÈTE"
check "  Dokos was sent all the same" equals "$(( $(snapshots --tag "dokopilot,$prod_tag") >= before ))" 1
check "  Forgejo was restarted" contains "$(cat "$FAKE/docker.log")" "start forgejo"

if (( failures > 0 )); then
  echo "$failures check(s) failed (last output of a script:)"
  out | tail -n 20
  exit 1
fi
echo "All backup script checks passed"
