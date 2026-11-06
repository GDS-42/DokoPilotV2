#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Local mode test ("everything on this machine", docs/plan-v0.2.md C-6). A Debian 13
# container with systemd as PID 1 plays a machine installed from its console: the person's
# account (camille) has a password, is in the sudo group, has a password-less sudo rule
# (cloud-init) and the SSH key of a laptop; the SSH server is not installed.
#   1. install.sh --check, then install.sh --answers in local mode, as camille, up to config;
#   2. the maintenance role refuses a local run when the account has no usable password;
#   3. system.yml run by camille on the machine itself (connection local, sudo with her own
#      password, as bin/dokopilot -K gives it), twice: the second run changes nothing;
#   4. her password is unchanged, sudo asks for it in a new session (and only in the
#      maintenance group), the password-less rule is gone, the SSH server is installed and
#      hardened, the laptop key still opens a session, password logins are refused, the
#      firewall is up, the host part of check.yml passes, and no secret is in the logs.
# Environment: DP_TEST_KEEP=1 to keep the container and the work files,
# DP_TEST_SUBNET_PREFIX (default 172.30.95, a /24), DP_TEST_CA_FILE (extra CA of an HTTPS
# proxy, default $SSL_CERT_FILE).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
# shellcheck source=tests/installer/lib.sh
. "$here/lib.sh"

image="dokopilot-installer-local:trixie"
container="dokopilot-installer-local"
network="dokopilot-installer-local"
subnet_prefix="${DP_TEST_SUBNET_PREFIX:-172.30.95}"
gateway="$subnet_prefix.1"
machine_ip="$subnet_prefix.10"
client=atelier-local
work="$(mktemp -d)"

cleanup() {
  local rc=$?
  cleanup_relay
  if [[ "${DP_TEST_KEEP:-0}" == 1 ]]; then
    echo "Kept: container $container, network $network, work files in $work"
  else
    docker rm -f -v "$container" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    rm -rf "$work"
  fi
  exit "$rc"
}
trap cleanup EXIT

command -v docker >/dev/null || fail "docker is required"
command -v ssh-keygen >/dev/null || fail "ssh-keygen is required"

on_machine() { docker exec "$container" "$@"; }
on_machine_stdin() { docker exec -i "$container" "$@"; }

camille_password="$(random_token)"
model_key="sk-test-$(random_token)"

# as_camille ARGS...: a command run by camille herself (docker exec gives the groups of
# /etc/group at that moment, like a new login).
as_camille() {
  docker exec -u camille -w /home/camille -e HOME=/home/camille -e USER=camille -e LANG=C.UTF-8 \
    -e DOKOPILOT_REPO_URL=file:///srv/dokopilot -e DOKOPILOT_LOCAL_MIN_DISK_GB=1 -e "DP_TEST_MODEL_KEY=$model_key" \
    "${proxy_opts[@]}" "$container" "$@"
}

# playbook NAME PLAYBOOK [ARGS...]: run by camille on the machine itself, as bin/dokopilot
# runs it in local mode (connection local, no become password variable, the password typed
# for -K: here a private file). Log in $work/NAME.log.
playbook() {
  local name="$1" book="$2"
  shift 2
  # shellcheck disable=SC2016 # expanded inside the container
  as_camille env ANSIBLE_CONFIG=/home/camille/DokoPilotV2/ansible/ansible.cfg ANSIBLE_FORCE_COLOR=0 \
    sh -c 'cd DokoPilotV2/ansible && exec ../.venv/bin/ansible-playbook -i ~/t/inventory.yml -e @$HOME/t/client.yml \
      -e @$HOME/t/secrets.yml -e "dokopilot_client_dir=$HOME/DokoPilotV2/clients/'"$client"'" -e base_swap_size_mb=0 \
      --become-password-file ~/t/become "$@"' playbook "$@" "$book" > "$work/$name.log" 2>&1
}

must_run() {
  local name="$1"
  if ! playbook "$@"; then
    tail -n 60 "$work/$name.log"
    fail "playbook run '$name' failed (log above)"
  fi
  ok "playbook run '$name'"
}

assert_no_change() {
  local name="$1" changed
  changed="$(sed -n "s/^${client}[[:space:]]*:.*changed=\\([0-9][0-9]*\\).*/\\1/p" "$work/$name.log" | tail -n 1)"
  if [[ "$changed" != 0 ]]; then
    awk '/^TASK \[/ { task = $0 } /^changed: / { print task }' "$work/$name.log" >&2
    fail "run '$name' is not idempotent: changed=${changed:-?} (changed tasks above)"
  fi
  ok "run '$name' changed nothing (idempotent)"
}

# ------------------------------------------------------------------ machine
step "Test network and image"
docker rm -f -v "$container" >/dev/null 2>&1 || true
docker network rm "$network" >/dev/null 2>&1 || true
docker network create --subnet "$subnet_prefix.0/24" --gateway "$gateway" "$network" >/dev/null
start_relay "$gateway"
mapfile -t proxy_opts < <(proxy_env)
mkdir "$work/build"
cp "$here/Dockerfile.local" "$work/build/Dockerfile"
build_args=()
if [[ -n "$proxy_url" ]]; then
  build_args+=(--network host --build-arg "APT_HTTPS_PROXY=$proxy_url")
fi
if [[ -n "$proxy_url" && -n "$ca_file" ]]; then
  cp "$ca_file" "$work/build/extra-ca.crt"
else
  : > "$work/build/extra-ca.crt"
fi
docker build --quiet "${build_args[@]}" -t "$image" "$work/build" >/dev/null
ok "image $image built"

step "Machine"
docker run -d --name "$container" --hostname dp-local-test --network "$network" --ip "$machine_ip" \
  --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock \
  -v /var/lib/docker -v /var/lib/containerd --memory "${DP_TEST_MEMORY:-3g}" "$image" >/dev/null
# Docker bind-mounts these files; make them ordinary files, as on a VM.
# shellcheck disable=SC2016 # expanded by the shell inside the container
on_machine bash -ec 'for f in /etc/hosts /etc/hostname /etc/resolv.conf; do
  content="$(cat "$f")"; umount "$f"; printf "%s\n" "$content" > "$f"; done'
state=""
for _ in $(seq 60); do
  state="$(on_machine systemctl is-system-running 2>/dev/null || true)"
  [[ "$state" == running || "$state" == degraded ]] && break
  sleep 1
done
ok "systemd ($state) running in $container ($machine_ip)"
install_ca "$container"
# Containers share the host clock; when it runs behind, apt (sqv) rejects freshly signed
# Release files. Test only, as in tests/system/run.sh: move sqv's limit by the measured skew.
server_date="$(curl -sSI --max-time 10 https://deb.debian.org/ 2>/dev/null | sed -n 's/^[Dd]ate: //p' | tr -d '\r' || true)"
skew=0
if [[ -n "$server_date" ]] && server_epoch="$(date -u -d "$server_date" +%s 2>/dev/null)"; then
  skew=$(( server_epoch - $(date -u +%s) ))
fi
if (( skew > 120 )); then
  on_machine_stdin sh -ec 'dpkg-divert --quiet --local --rename --divert /usr/bin/sqv.real --add /usr/bin/sqv
    cat > /usr/bin/sqv; chmod 755 /usr/bin/sqv
    echo "Acquire::Check-Date \"false\";" > /etc/apt/apt.conf.d/99dokopilot-test-clock' <<EOF
#!/bin/sh
# DokoPilot installer test only: compensates a host clock that is $skew s behind.
exec /usr/bin/sqv.real --not-after "\$(date -u -d "@\$((\$(date +%s) + $skew + 600))" +%Y-%m-%dT%H:%M:%SZ)" "\$@"
EOF
fi

# The person's password and the key of her laptop (as if she had copied it with ssh-copy-id).
printf 'camille:%s\n' "$camille_password" | on_machine_stdin chpasswd
ssh-keygen -q -t ed25519 -N '' -C camille@portable -f "$work/laptop.key"
on_machine install -d -m 700 -o camille -g camille /home/camille/.ssh
on_machine_stdin sh -ec 'cat > /home/camille/.ssh/authorized_keys; chown camille:camille /home/camille/.ssh/authorized_keys
  chmod 600 /home/camille/.ssh/authorized_keys' < "$work/laptop.key.pub"
on_machine dpkg -s openssh-server >/dev/null 2>&1 && fail "test setup: the SSH server is already installed"
groups_before="$(on_machine id -nG camille)"
ok "camille: password set, groups « $groups_before », laptop key authorized, no SSH server"

step "Working tree snapshot and answers"
make_snapshot "$work/snapshot"
docker cp "$work/snapshot" "$container:/srv/dokopilot" >/dev/null
cat > "$work/answers.yml" <<EOF
stop_after: config
where: local
profile: demo
client_id: $client
harness: opencode
models:
  provider: mistral
  api_key_env: DP_TEST_MODEL_KEY
people:
  - first_name: Camille
    last_name: Martin
    email: camille.martin@atelier-demo.test
network: lan
EOF
docker cp "$work/answers.yml" "$container:/home/camille/reponses.yml" >/dev/null
on_machine sh -ec 'chown -R camille:camille /srv/dokopilot /home/camille/reponses.yml
  cp /srv/dokopilot/install.sh /home/camille/install.sh; chown camille:camille /home/camille/install.sh'
ok "snapshot $(git -C "$work/snapshot" rev-parse --short HEAD) and answers in place"

# ------------------------------------------------------------------ installer
step "install.sh --check, then install.sh --answers in local mode (up to config)"
as_camille sh install.sh --check > "$work/check.log" 2>&1 || { cat "$work/check.log"; fail "install.sh --check failed"; }
expect_contains "--check: this machine can hold everything" "$(cat "$work/check.log")" \
  '✓ Système : Debian GNU/Linux 13' '« Tout sur cette machine » : possible'
if ! as_camille sh install.sh --version installer-test --answers reponses.yml > "$work/install.log" 2>&1; then
  tail -n 60 "$work/install.log"
  fail "install.sh --answers failed in local mode"
fi
expect_contains "install.sh in local mode: account kept as maintenance account, configuration validated" "$(cat "$work/install.log")" \
  'Votre compte « camille » devient le compte de maintenance : son mot de passe ne change pas' \
  'Les 1 clé\(s\) SSH déjà autorisées pour « camille »' "Configuration de « $client » validée"
local_yml="$(as_camille cat "DokoPilotV2/clients/$client/client.local.yml")"
expect_contains "client.local.yml: local server, camille with the laptop key, host name kept" "$local_yml" \
  '^  local: true$' '^  address: localhost$' '^  hostname: dp-local-test$' '^    - name: camille$' 'camille@portable'

# ------------------------------------------------------------------ vars for the playbooks
# What bin/dokopilot gives Ansible in local mode: client.yml merged with client.local.yml, the
# decrypted secrets (0600) and an inventory with connection local and no become password.
as_camille sh -ec "umask 077; mkdir -p ~/t
  printf 'dokopilot:\n  hosts:\n    $client:\n      ansible_connection: local\n' > ~/t/inventory.yml
  age --decrypt --identity ~/.config/dokopilot/age-identity.txt DokoPilotV2/clients/$client/secrets.yml.age > ~/t/secrets.yml
  DokoPilotV2/.venv/bin/python -c '
import sys, yaml
def merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
base, local = (yaml.safe_load(open(p, encoding=\"utf-8\")) for p in sys.argv[1:3])
yaml.safe_dump(merge(base, local), open(sys.argv[3], \"w\", encoding=\"utf-8\"), allow_unicode=True)
' DokoPilotV2/clients/$client/client.yml DokoPilotV2/clients/$client/client.local.yml ~/t/client.yml"
printf '%s\n' "$camille_password" | docker exec -i -u camille "$container" sh -ec 'umask 077; cat > /home/camille/t/become'
sudo_secret="$(as_camille sh -c "sed -n 's/^  maintenance_sudo_password: //p' ~/t/secrets.yml")"
[[ -n "$sudo_secret" ]] || fail "test setup: no maintenance_sudo_password in the secrets"
printf '%s\n' "$camille_password" "$model_key" "$sudo_secret" > "$work/secret-values.txt"
shadow_before="$(on_machine getent shadow camille | cut -d: -f2)"
ok "inventory, merged settings, secrets and become password ready"

# ------------------------------------------------------------------ guard
step "The maintenance role refuses a local run when the account has no usable password"
on_machine passwd --quiet --lock camille
sudoers_state() { on_machine sh -c 'id -nG camille; ls /etc/sudoers.d; cat /etc/sudoers.d/90-cloud-init-users'; }
before="$(sudoers_state)"
if playbook guard ../tests/system/role.yml -e dokopilot_test_role=maintenance; then
  fail "the maintenance role accepted a local run with a locked password"
fi
expect_contains "the refusal is in French" "$(tr -s ' \n' ' ' < "$work/guard.log")" \
  "le compte « camille » n.a pas de mot de passe utilisable" 'Aucun compte n.a été modifié'
[[ "$(sudoers_state)" == "$before" ]] || fail "the refused run changed the groups or the sudo rules"
on_machine passwd --quiet --unlock camille
[[ "$(on_machine getent shadow camille | cut -d: -f2)" == "$shadow_before" ]] || fail "test setup: unlock did not restore the hash"
ok "nothing changed; password unlocked again"

# ------------------------------------------------------------------ system.yml
step "system.yml run by camille on the machine itself (first run)"
must_run system-1 playbooks/system.yml
step "system.yml again: no change expected"
must_run system-2 playbooks/system.yml
assert_no_change system-2

step "camille's account"
[[ "$(on_machine getent shadow camille | cut -d: -f2)" == "$shadow_before" ]] || fail "camille's password was changed"
ok "camille's password is unchanged"
groups_after="$(on_machine id -nG camille)"
[[ "$groups_after" == "camille dokopilot-maint" ]] || fail "camille's groups are '$groups_after' (expected only dokopilot-maint)"
ok "camille is in dokopilot-maint only (sudo, audio… removed, never docker)"
cloud_rules="$(on_machine cat /etc/sudoers.d/90-cloud-init-users)"
grep -q '^camille .*NOPASSWD' <<< "$cloud_rules" && fail "camille's password-less sudo rule is still there"
expect_contains "the password-less rule of camille is removed, the unrelated rule kept" "$cloud_rules" \
  '^backup ALL=\(root\) NOPASSWD: /usr/bin/true$'
if on_machine su - camille -c 'sudo -n true' 2>/dev/null; then
  fail "sudo works without a password for camille in a new session"
fi
ok "a new session of camille cannot use sudo without her password"
[[ "$(on_machine_stdin su - camille -c "sudo -S -k -p '' id -un" <<< "$camille_password")" == root ]] \
  || fail "sudo with camille's own password does not work in a new session"
ok "sudo with camille's own password works in a new session"

step "SSH and firewall"
on_machine dpkg -s openssh-server >/dev/null 2>&1 || fail "the SSH server was not installed"
on_machine systemctl is-active --quiet ssh || fail "the SSH server is not running"
expect_contains "the SSH server is installed and hardened" "$(on_machine sshd -T -C "user=camille,host=test,addr=$gateway")" \
  '^permitrootlogin no$' '^passwordauthentication no$' '^kbdinteractiveauthentication no$' \
  '^authenticationmethods publickey$' '^allowgroups dokopilot-maint$' '^maxauthtries 3$'
ssh_opts=(-o BatchMode=yes -o ControlPath=none -o ConnectTimeout=10 -o UserKnownHostsFile="$work/known_hosts" -o StrictHostKeyChecking=accept-new)
[[ "$(ssh "${ssh_opts[@]}" -o IdentitiesOnly=yes -i "$work/laptop.key" "camille@$machine_ip" id -un)" == camille ]] \
  || fail "the laptop key no longer opens a session for camille"
ok "the laptop key still opens an SSH session for camille"
password_attempt="$(ssh "${ssh_opts[@]}" -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive \
  "camille@$machine_ip" true 2>&1 || true)"
expect_contains "SSH refuses passwords" "$password_attempt" 'Permission denied \(publickey\)'
expect_contains "the firewall table is up, loopback accepted" "$(on_machine nft list table inet dokopilot)" \
  'policy drop;' 'iif "lo" accept'

step "check.yml, host part"
must_run host-checks playbooks/check.yml --tags host -e host_checks_backup=false
if grep -q 'ÉCHEC' "$work/host-checks.log"; then
  grep 'ÉCHEC' "$work/host-checks.log" >&2
  fail "a host check failed in local mode"
fi
ok "check.yml host part passes in local mode"

if grep -qF -f "$work/secret-values.txt" "$work"/*.log; then
  fail "a secret (camille's password, the sudo password of the secrets or the model key) appears in a log"
fi
ok "no secret in the logs"

step "All local mode checks passed"
