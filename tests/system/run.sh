#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# System roles test. A Debian 13 container with systemd as PID 1 plays the server, and the
# playbooks reach it over SSH as they would a VPS:
#   1. bootstrap.yml with the provider's account (password-less sudo, as on a cloud image);
#   2. system.yml with a maintenance account and its sudo password, twice: the second run
#      must report no change; then system.yml in check mode;
#   3. checks of SSH, sudo, nftables, fail2ban, Docker, updates and base settings;
#   4. the proxy role (Caddy, lan mode), twice, then requests through it;
#   5. retirement of a maintenance account (system.yml without it, twice);
#   6. the tailscale role without auth key, which must stop with its French message;
#   7. with DP_TEST_SITE=1, the full installation as a user runs it on a VM: site.yml twice
#      (Dokos stack, demo data, backups, proxy), check.yml, harness.yml reading the real
#      assistant token on the server, MCP through Caddy, a backup; the Dokos image is pulled
#      and built inside the test server as on a VM (needs ~6 GB RAM, ~10 GB disk, 30-45 min).
# Environment: ANSIBLE_PLAYBOOK (default .venv/bin/ansible-playbook), DP_TEST_KEEP=1 to keep
# the container and the work files, DP_TEST_SUBNET_PREFIX (default 172.30.99, a /24),
# DP_TEST_CA_FILE (extra CA for an HTTPS proxy, default $SSL_CERT_FILE).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
playbook_bin="${ANSIBLE_PLAYBOOK:-$repo/.venv/bin/ansible-playbook}"
image="dokopilot-system-test:trixie"
container="dokopilot-system-test"
network="dokopilot-system-test"
subnet_prefix="${DP_TEST_SUBNET_PREFIX:-172.30.99}"
gateway="$subnet_prefix.1"
server_ip="$subnet_prefix.10"
forward_port=3128
work="$(mktemp -d)"
forwarder_pid=""

cleanup() {
  local rc=$?
  if [[ -n "$forwarder_pid" ]]; then
    kill "$forwarder_pid" 2>/dev/null || true
  fi
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

step() { printf '\n=== %s\n' "$*"; }
ok() { printf 'ok    %s\n' "$*"; }
fail() {
  printf 'FAIL  %s\n' "$*" >&2
  exit 1
}

# Runs a command inside the test server, as root.
on_server() { docker exec "$container" "$@"; }
# Same, with this script's standard input forwarded.
on_server_stdin() { docker exec -i "$container" "$@"; }

# ssh_as USER KEY [COMMAND...]: a fresh SSH connection (no multiplexing) to the test server.
ssh_as() {
  local user="$1" key="$2"
  shift 2
  ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ControlMaster=no -o ControlPath=none \
    -o UserKnownHostsFile="$work/known_hosts" -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=10 -i "$key" "$user@$server_ip" "$@"
}

# run_playbook NAME INVENTORY CLIENT_FILE PLAYBOOK [ARGS...]: log in $work/NAME.log.
run_playbook() {
  local name="$1" inventory="$2" client="$3" playbook="$4"
  shift 4
  (cd "$repo/ansible" && ANSIBLE_CONFIG="$repo/ansible/ansible.cfg" ANSIBLE_FORCE_COLOR=0 \
    ANSIBLE_SSH_CONTROL_PATH_DIR="$work/cp" ANSIBLE_LOCAL_TEMP="$work/ansible-tmp" \
    "$playbook_bin" -i "$inventory" -e "@$repo/clients/demo/client.yml" -e "@$client" \
    -e "@$work/secrets.yml" -e base_swap_size_mb=0 "$@" "$playbook") > "$work/$name.log" 2>&1
}

# Runs a playbook that must succeed; prints the end of its log otherwise.
must_run() {
  local name="$1"
  if ! run_playbook "$@"; then
    tail -n 60 "$work/$name.log"
    fail "playbook run '$name' failed (log above)"
  fi
  ok "playbook run '$name'"
}

# Fails when the run logged in $work/NAME.log changed anything, listing the changed tasks.
assert_no_change() {
  local name="$1" changed
  changed="$(sed -n 's/^demo[[:space:]]*:.*changed=\([0-9][0-9]*\).*/\1/p' "$work/$name.log" | tail -n 1)"
  if [[ "$changed" != 0 ]]; then
    awk '/^TASK \[/ { task = $0 } /^changed: / { print task }' "$work/$name.log" >&2
    fail "run '$name' is not idempotent: changed=${changed:-?} (changed tasks above)"
  fi
  ok "run '$name' changed nothing (idempotent)"
}

# expect_contains LABEL TEXT PATTERN...: every extended regex must match TEXT.
expect_contains() {
  local label="$1" text="$2"
  shift 2
  local pattern
  for pattern in "$@"; do
    if ! grep -Eq -- "$pattern" <<< "$text"; then
      printf '%s\n' "$text" >&2
      fail "$label: '$pattern' not found (output above)"
    fi
  done
  ok "$label"
}

expect_ssh_refused() {
  local label="$1" user="$2" key="$3"
  if ssh_as "$user" "$key" true 2>/dev/null; then
    fail "$label: SSH login as $user succeeded"
  fi
  ok "$label"
}

# ------------------------------------------------------------------ preconditions
command -v docker >/dev/null || fail "docker is required"
command -v ssh-keygen >/dev/null || fail "ssh-keygen is required"
[[ -x "$playbook_bin" ]] || fail "ansible-playbook not found at $playbook_bin: run make setup or set ANSIBLE_PLAYBOOK"

step "Vendored repository signing keys"
if command -v gpg >/dev/null; then
  mkdir -m 700 "$work/gnupg"
  for entry in docker/files/docker-archive-keyring.asc:9DC858229FC7DD38854AE2D88D81803C0EBFCD88 \
    tailscale/files/tailscale-archive-keyring.asc:2596A99EAAB33821893C0A79458CA832957F5868; do
    file="$repo/ansible/roles/${entry%%:*}"
    fingerprint="$(GNUPGHOME="$work/gnupg" gpg --batch --show-keys --with-colons "$file" 2>/dev/null \
      | awk -F: '$1 == "fpr" { print $10; exit }')"
    [[ "$fingerprint" == "${entry##*:}" ]] || fail "$file: fingerprint $fingerprint, expected ${entry##*:}"
    ok "$(basename "$file") has fingerprint $fingerprint"
  done
else
  echo "skip  gpg absent: fingerprints of the vendored keys not checked"
fi

# ------------------------------------------------------------------ test server
step "Test network and image"
docker rm -f -v "$container" >/dev/null 2>&1 || true
docker network rm "$network" >/dev/null 2>&1 || true
docker network create --subnet "$subnet_prefix.0/24" --gateway "$gateway" "$network" >/dev/null

# A proxy on the host's loopback is out of the container's reach: relay it from the gateway.
proxy="${HTTPS_PROXY:-${https_proxy:-}}"
apt_proxy=""
build_args=()
if [[ -n "$proxy" ]]; then
  hostport="${proxy#*://}"
  hostport="${hostport%%/*}"
  hostport="${hostport##*@}"
  proxy_host="${hostport%:*}"
  proxy_port="${hostport##*:}"
  if [[ "$proxy_host" == 127.* || "$proxy_host" == localhost ]]; then
    python3 "$here/tcp-forward.py" "$gateway" "$forward_port" "$proxy_host" "$proxy_port" &
    forwarder_pid=$!
    apt_proxy="http://$gateway:$forward_port"
  else
    apt_proxy="http://$hostport"
  fi
  build_args+=(--network host --build-arg "APT_HTTPS_PROXY=$apt_proxy")
  echo "apt goes through the HTTPS proxy $apt_proxy"
fi
mkdir "$work/build"
cp "$here/Dockerfile" "$work/build/"
ca_file="${DP_TEST_CA_FILE:-${SSL_CERT_FILE:-}}"
if [[ -n "$proxy" && -n "$ca_file" && -r "$ca_file" ]]; then
  cp "$ca_file" "$work/build/extra-ca.crt"
else
  : > "$work/build/extra-ca.crt"
fi
docker build --quiet "${build_args[@]}" -t "$image" "$work/build" >/dev/null
ok "image $image built"

step "Test server"
test_memory=2g
if [[ "${DP_TEST_SITE:-0}" == 1 ]]; then
  test_memory=6g
fi
docker run -d --name "$container" --hostname dp-test-original --network "$network" --ip "$server_ip" \
  --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock \
  -v /var/lib/docker -v /var/lib/containerd --memory "${DP_TEST_MEMORY:-$test_memory}" "$image" >/dev/null
# Docker bind-mounts these files; make them ordinary files, as on a VM.
# shellcheck disable=SC2016 # expanded by the shell inside the container
on_server bash -ec 'for f in /etc/hosts /etc/hostname /etc/resolv.conf; do
  content="$(cat "$f")"; umount "$f"; printf "%s\n" "$content" > "$f"; done'
for _ in $(seq 60); do
  state="$(on_server systemctl is-system-running 2>/dev/null || true)"
  [[ "$state" == running || "$state" == degraded ]] && break
  sleep 1
done
on_server systemctl is-active --quiet ssh || fail "sshd is not running in the test server (systemd state: $state)"
ok "systemd ($state) and sshd running in $container ($server_ip)"

# Containers share the host clock. When it runs behind, apt (sqv) rejects freshly signed
# Release files as "created after the --not-after date". Test only: compensate the measured
# skew by moving sqv's limit forward; nothing in the roles changes.
server_date="$(curl -sSI --max-time 10 https://deb.debian.org/ 2>/dev/null | sed -n 's/^[Dd]ate: //p' | tr -d '\r' || true)"
skew=0
if [[ -n "$server_date" ]] && server_epoch="$(date -u -d "$server_date" +%s 2>/dev/null)"; then
  skew=$(( server_epoch - $(date -u +%s) ))
fi
if (( skew > 120 )); then
  echo "The host clock is $skew s behind deb.debian.org: sqv accepts signatures up to that much in the future"
  on_server_stdin sh -ec 'dpkg-divert --quiet --local --rename --divert /usr/bin/sqv.real --add /usr/bin/sqv
    cat > /usr/bin/sqv; chmod 755 /usr/bin/sqv
    echo "Acquire::Check-Date \"false\";" > /etc/apt/apt.conf.d/99dokopilot-test-clock' <<EOF
#!/bin/sh
# DokoPilot system test only: compensates a host clock that is $skew s behind.
exec /usr/bin/sqv.real --not-after "\$(date -u -d "@\$((\$(date +%s) + $skew + 600))" +%Y-%m-%dT%H:%M:%SZ)" "\$@"
EOF
fi

for account in provider integrateur ancien nouveau; do
  ssh-keygen -q -t ed25519 -N '' -C "$account@dokopilot-test" -f "$work/$account.key"
done
for home in /home/provider /root; do
  owner="$(basename "$home")"
  on_server_stdin sh -ec "install -d -m 700 -o $owner -g $owner $home/.ssh
    cat > $home/.ssh/authorized_keys; chown $owner:$owner $home/.ssh/authorized_keys
    chmod 600 $home/.ssh/authorized_keys" < "$work/provider.key.pub"
done
ssh_as root "$work/provider.key" true || fail "root cannot log in before the bootstrap (test setup)"
ok "provider and root log in with the provider key before the bootstrap"

# ------------------------------------------------------------------ configuration
sudo_password="$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
umask 077
cat > "$work/secrets.yml" <<EOF
secrets:
  maintenance_sudo_password: "$sudo_password"
  db_root_password: "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
  dokos_admin_password: "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
  entrepreneur_password: "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
  restic_password: "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
  restic_env: {}
  tailscale_auth_key: ""
  etransactions: {}
  ai_providers: {}
EOF
umask 022

# write_client FILE ACCOUNTS...: settings layered over clients/demo/client.yml.
write_client() {
  local file="$1" account
  shift
  {
    printf 'server:\n  address: %s\n  hostname: dokopilot-demo-test\n  bootstrap_user: provider\n' "$server_ip"
    printf 'maintenance:\n  sudo_requires_password: true\n'
    printf '  ssh_allowed_from: ["%s/32", "fd00:dead:beef::/64"]\n  users:\n' "$gateway"
    for account in "$@"; do
      printf '    - name: %s\n      full_name: "Compte de test %s"\n' "$account" "$account"
      printf '      ssh_public_key_files: ["%s"]\n' "$work/$account.key.pub"
    done
  } > "$file"
}
write_client "$work/client.yml" integrateur ancien
write_client "$work/client-retired.yml" integrateur
cp "$work/client-retired.yml" "$work/client-tailscale.yml"
printf 'network:\n  mode: tailscale\n' >> "$work/client-tailscale.yml"
# Lockout guards: a source list without the integrator's address, a maintainer without account.
sed 's|^  ssh_allowed_from: .*|  ssh_allowed_from: ["198.51.100.0/24"]|' "$work/client.yml" > "$work/client-fw-guard.yml"
write_client "$work/client-ssh-guard.yml" integrateur ancien nouveau

# Inventories shaped like the ones bin/dokopilot generates.
ssh_args="-o UserKnownHostsFile=$work/known_hosts -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
cat > "$work/inventory-bootstrap.yml" <<EOF
dokopilot:
  hosts:
    demo:
      ansible_host: $server_ip
      ansible_port: 22
      ansible_user: provider
      ansible_ssh_private_key_file: $work/provider.key
      ansible_ssh_common_args: "$ssh_args"
EOF
cat > "$work/inventory.yml" <<EOF
dokopilot:
  hosts:
    demo:
      ansible_host: $server_ip
      ansible_port: 22
      ansible_user: integrateur
      ansible_ssh_private_key_file: $work/integrateur.key
      ansible_ssh_common_args: "$ssh_args"
      ansible_become_password: "{{ secrets.maintenance_sudo_password }}"
EOF

# ------------------------------------------------------------------ bootstrap
step "bootstrap.yml with the provider account"
must_run bootstrap "$work/inventory-bootstrap.yml" "$work/client.yml" playbooks/bootstrap.yml
expect_contains "bootstrap warns about the provider account and ends with the French instructions" \
  "$(tr -s ' \n' ' ' < "$work/bootstrap.log")" "pas un compte de maintenance" "Amorçage terminé"
sudoers_cloud="$(on_server cat /etc/sudoers.d/90-cloud-init-users)"
grep -q '^integrateur .*NOPASSWD' <<< "$sudoers_cloud" && fail "bootstrap kept the NOPASSWD rule of integrateur"
grep -q '^provider .*NOPASSWD' <<< "$sudoers_cloud" || fail "bootstrap removed the NOPASSWD rule of the account it runs with"
ok "bootstrap removed the NOPASSWD rule of integrateur and kept the provider's for its own run"
expect_ssh_refused "provider can no longer log in after the bootstrap" provider "$work/provider.key"

# ------------------------------------------------------------------ system.yml
step "system.yml with the maintenance account (first run)"
must_run system-1 "$work/inventory.yml" "$work/client.yml" playbooks/system.yml
step "system.yml again: no change expected"
must_run system-2 "$work/inventory.yml" "$work/client.yml" playbooks/system.yml
assert_no_change system-2
step "system.yml in check mode: no change, no failure"
must_run system-check "$work/inventory.yml" "$work/client.yml" playbooks/system.yml --check
assert_no_change system-check

step "SSH"
sshd_t="$(on_server sshd -T -C "user=integrateur,host=test,addr=$gateway")"
expect_contains "sshd -T has the hardened values" "$sshd_t" \
  '^port 22$' '^permitrootlogin no$' '^passwordauthentication no$' '^kbdinteractiveauthentication no$' \
  '^pubkeyauthentication yes$' '^authenticationmethods publickey$' '^allowgroups dokopilot-maint$' \
  '^maxauthtries 3$' '^logingracetime 30$' '^x11forwarding no$' '^allowagentforwarding no$' \
  '^allowtcpforwarding local$' '^permittunnel no$' '^clientaliveinterval 300$' \
  '^clientalivecountmax 2$' '^authorizedkeysfile \.ssh/authorized_keys$'
grep -E '^(permitrootlogin|passwordauthentication|allowgroups) ' <<< "$sshd_t"
expect_ssh_refused "root login refused (even with a valid key)" root "$work/provider.key"
expect_ssh_refused "provider login refused" provider "$work/provider.key"
[[ "$(ssh_as integrateur "$work/integrateur.key" id -un)" == integrateur ]] || fail "integrateur cannot log in"
ok "integrateur logs in with its key"
[[ "$(ssh_as ancien "$work/ancien.key" id -un)" == ancien ]] || fail "ancien cannot log in"
ok "ancien logs in with its key"
password_attempt="$(ssh -o BatchMode=yes -o PubkeyAuthentication=no -o ControlPath=none \
  -o PreferredAuthentications=password,keyboard-interactive -o UserKnownHostsFile="$work/known_hosts" \
  "integrateur@$server_ip" true 2>&1 || true)"
expect_contains "password authentication is not offered" "$password_attempt" 'Permission denied \(publickey\)'

step "sudo"
if ssh_as integrateur "$work/integrateur.key" sudo -n true 2>/dev/null; then
  fail "sudo works without password for integrateur"
fi
ok "sudo without password refused"
[[ "$(ssh_as integrateur "$work/integrateur.key" "sudo -S -p '' id -un" <<< "$sudo_password")" == root ]] \
  || fail "sudo with the password from the secrets does not work"
ok "sudo with the password from the secrets works"
groups_integrateur="$(on_server id -nG integrateur)"
[[ "$groups_integrateur" == "integrateur dokopilot-maint" ]] \
  || fail "integrateur groups are '$groups_integrateur' (expected only dokopilot-maint, never docker or sudo)"
ok "integrateur is only in dokopilot-maint (not docker, not sudo)"
sudoers_cloud="$(on_server cat /etc/sudoers.d/90-cloud-init-users)"
grep -Eq '^(provider|integrateur) .*NOPASSWD' <<< "$sudoers_cloud" && fail "NOPASSWD rules left: $sudoers_cloud"
expect_contains "other NOPASSWD rules removed, unrelated rule kept" "$sudoers_cloud" '^backup ALL=\(root\) NOPASSWD: /usr/bin/true$'
expect_contains "sudoers file and sudo log" "$(on_server cat /etc/sudoers.d/dokopilot-maintenance /var/log/sudo.log)" \
  '^Defaults use_pty$' '^Defaults:%dokopilot-maint timestamp_timeout=15$' '^%dokopilot-maint ALL=\(ALL:ALL\) ALL$' \
  ' : integrateur : ' 'COMMAND=/usr/bin/id -un'

step "Firewall"
nft_table="$(on_server nft list table inet dokopilot)"
expect_contains "table inet dokopilot drops by default and opens SSH and HTTP to the allowed ranges" "$nft_table" \
  'type filter hook input priority filter; policy drop;' \
  'ct state vmap \{ (established : accept, related : accept, invalid : drop|invalid : drop, established : accept, related : accept) \}' \
  "elements = \\{ $gateway \\}" 'elements = \{ fd00:dead:beef::/64 \}' \
  'tcp dport 22 ip saddr @ssh_allowed_v4 accept' 'tcp dport 22 ip6 saddr @ssh_allowed_v6 accept' \
  'tcp dport 80 ip saddr @lan_allowed_v4 accept'
grep -E 'policy|dport' <<< "$nft_table"
grep -q 'tailscale0' <<< "$nft_table" && fail "tailscale rules present in lan mode"
tables_before="$(on_server nft list tables)"
expect_contains "Docker's tables coexist with ours" "$tables_before" '^table inet dokopilot$' '^table ip (nat|filter)$'
on_server systemctl restart nftables
tables_after="$(on_server nft list tables)"
[[ "$tables_before" == "$tables_after" ]] || fail "restarting nftables changed the tables: $tables_after"
ok "restarting nftables.service keeps the Docker tables (no flush ruleset)"

step "fail2ban"
f2b_status="$(on_server fail2ban-client status sshd)"
expect_contains "fail2ban-client status sshd" "$f2b_status" 'Currently failed' 'Journal matches:.*sshd-session'
expect_contains "fail2ban ignores the allowed SSH sources" "$(on_server fail2ban-client get sshd ignoreip)" "$gateway\$"
expect_contains "fail2ban bans with nftables for 1 h, with increment" \
  "$(on_server fail2ban-client get sshd actions; on_server fail2ban-client get sshd bantime; on_server fail2ban-client get sshd maxretry)" \
  'nftables-multiport' '^3600$' '^5$'
# Real attempts can only come from the gateway (in ignoreip) or the server itself (ignoreself).
# Five failures from a documentation address are written to the journal the way OpenSSH 10
# does (identifier and process name sshd-session), which must trigger a ban.
attacker=203.0.113.7
# One process sends the five lines and stays alive a moment: journald reads _COMM from /proc,
# so a sender that has already exited would leave entries the jail does not match.
on_server sh -ec "cp /usr/bin/logger /tmp/sshd-session
  { for i in 1 2 3 4 5; do echo \"Invalid user intrus\$i from $attacker port 4000\$i\"; done; sleep 3; } \\
    | /tmp/sshd-session -t sshd-session -p auth.info"
banned=""
for _ in $(seq 20); do
  banned="$(on_server fail2ban-client status sshd)"
  grep -q "Banned IP list:.*$attacker" <<< "$banned" && break
  sleep 1
done
expect_contains "fail2ban reads sshd-session failures from the journal and bans after 5" "$banned" \
  'Total failed:[[:space:]]+[5-9]' "Banned IP list:.*$attacker"
expect_contains "the ban is enforced in fail2ban's own nftables table" "$(on_server nft list table inet f2b-table)" "$attacker"
on_server fail2ban-client set sshd unbanip "$attacker" >/dev/null

step "Docker"
docker_info="$(on_server docker info --format '{{.ServerVersion}} {{.Driver}} {{.LoggingDriver}} live-restore={{.LiveRestoreEnabled}} {{.OSType}}/{{.Architecture}}')"
expect_contains "docker info" "$docker_info" ' local live-restore=true linux/'
echo "$docker_info"
expect_contains "daemon.json" "$(on_server cat /etc/docker/daemon.json)" '"userland-proxy": false' '"max-size": "10m"'
expect_contains "Docker repository for the right architecture" "$(on_server cat /etc/apt/sources.list.d/docker.sources)" \
  '^URIs: https://download.docker.com/linux/debian$' '^Suites: trixie$' "^Architectures: $(on_server dpkg --print-architecture)$"

step "Base system"
expect_contains "host name, time zone, locale" \
  "$(on_server hostname; on_server grep 127.0.1.1 /etc/hosts; on_server timedatectl show -p Timezone --value; on_server cat /etc/default/locale; on_server locale -a)" \
  '^dokopilot-demo-test$' '^127\.0\.1\.1 dokopilot-demo-test dokopilot-demo-test$' '^Europe/Paris$' \
  '^LANG=fr_FR\.UTF-8$' '^fr_FR\.utf8$' '^en_US\.utf8$'
expect_contains "kernel settings" "$(on_server sysctl net.ipv4.ip_forward kernel.dmesg_restrict kernel.kptr_restrict \
  net.ipv4.conf.all.rp_filter net.ipv4.conf.all.accept_redirects net.ipv4.tcp_syncookies fs.protected_symlinks)" \
  '^net.ipv4.ip_forward = 1$' '^kernel.dmesg_restrict = 1$' '^kernel.kptr_restrict = 1$' \
  '^net.ipv4.conf.all.rp_filter = 2$' '^net.ipv4.conf.all.accept_redirects = 0$' '^net.ipv4.tcp_syncookies = 1$' \
  '^fs.protected_symlinks = 1$'
expect_contains "journald size limit" "$(on_server systemd-analyze cat-config systemd/journald.conf)" '^SystemMaxUse=500M$'
uu="$(on_server unattended-upgrade --dry-run --debug 2>&1 | grep -E '^Allowed origins' || true)"
expect_contains "unattended-upgrades origins" "$uu" 'origin=Debian,codename=trixie,label=Debian-Security' \
  'origin=Docker,label=Docker CE' 'origin=Tailscale,label=Tailscale'
expect_contains "no automatic reboot" "$(on_server apt-config dump Unattended-Upgrade::Automatic-Reboot)" \
  'Automatic-Reboot "false"'

# ------------------------------------------------------------------ proxy
step "proxy role (lan mode, Caddy), standalone"
must_run proxy-1 "$work/inventory.yml" "$work/client.yml" "$here/role.yml" -e dokopilot_test_role=proxy
expect_contains "dokopilot_dokos_url" "$(cat "$work/proxy-1.log")" "dokopilot_dokos_url=http://$server_ip\$"
must_run proxy-2 "$work/inventory.yml" "$work/client.yml" "$here/role.yml" -e dokopilot_test_role=proxy
assert_no_change proxy-2
code="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' --max-time 10 "http://$server_ip/")"
[[ "$code" == 502 ]] || fail "Caddy answered $code without Dokos (expected 502)"
ok "Caddy answers on port 80 (502 while Dokos is absent)"
# A stand-in for the Dokos frontend, published on 127.0.0.1:8080 like the real one. It runs
# from the test image itself (Python's web server), so no registry is needed.
docker save "$image" | on_server_stdin docker load --quiet >/dev/null
on_server docker run -d --name dokos-stub -p 127.0.0.1:8080:80 "$image" \
  sh -c 'mkdir -p /www && echo dokopilot-stub > /www/index.html && exec python3 -m http.server 80 --directory /www' >/dev/null
for _ in $(seq 20); do
  curl --noproxy '*' -fs -o /dev/null --max-time 2 "http://$server_ip/" && break
  sleep 1
done
response="$(curl --noproxy '*' -s -i --max-time 10 "http://$server_ip/")"
expect_contains "Caddy forwards to 127.0.0.1:8080 with security headers" "$response" \
  '^HTTP/1.1 200' 'dokopilot-stub' 'X-Content-Type-Options: nosniff' 'X-Frame-Options: SAMEORIGIN'
if curl --noproxy '*' -s -o /dev/null --max-time 5 "http://$server_ip:8080/"; then
  fail "the Dokos port 8080 is reachable from the network"
fi
ok "the Dokos port 8080 is not reachable from the network"
expect_contains "containers log with the local driver" \
  "$(on_server docker inspect -f '{{.HostConfig.LogConfig.Type}}' dokos-stub)" '^local$'

step "check.yml, host part"
must_run host-checks "$work/inventory.yml" "$work/client.yml" "$repo/ansible/playbooks/check.yml" \
  --tags host -e host_checks_backup=false
expect_contains "host checks report no failure" "$(cat "$work/host-checks.log")" \
  'OK +SSH : connexion root refusée' 'OK +Pare-feu' 'OK +Docker : aucun port' 'OK +Ports à l.+coute hors boucle locale'
if grep -q 'ÉCHEC' "$work/host-checks.log"; then
  grep 'ÉCHEC' "$work/host-checks.log" >&2
  fail "a host check failed"
fi
ok "check.yml host part passes"

# ------------------------------------------------------------------ lockout guards
step "Lockout guards"
# expect_refusal NAME CLIENT_FILE ROLE MESSAGE: the role must stop with MESSAGE.
expect_refusal() {
  local name="$1" client="$2" role="$3" message="$4"
  if run_playbook "$name" "$work/inventory.yml" "$client" "$here/role.yml" -e "dokopilot_test_role=$role"; then
    fail "$name: the $role role accepted settings that lock the integrator out"
  fi
  expect_contains "$name: the $role role refuses, in French" "$(tr -s ' \n' ' ' < "$work/$name.log")" "$message"
}
state_before="$(on_server sh -c 'nft list table inet dokopilot; md5sum /etc/nftables.conf /etc/ssh/sshd_config.d/*')"
expect_refusal guard-firewall "$work/client-fw-guard.yml" firewall "ne fait pas partie de maintenance.ssh_allowed_from"
expect_refusal guard-ssh "$work/client-ssh-guard.yml" ssh "Durcissement SSH interrompu, rien n.a été modifié"
[[ "$(on_server sh -c 'nft list table inet dokopilot; md5sum /etc/nftables.conf /etc/ssh/sshd_config.d/*')" == "$state_before" ]] \
  || fail "a refused run changed the firewall or the SSH settings"
ok "the refused runs left the firewall and the SSH settings untouched"

# ------------------------------------------------------------------ retirement
step "Retire a maintenance account"
must_run retire-1 "$work/inventory.yml" "$work/client-retired.yml" playbooks/system.yml
must_run retire-2 "$work/inventory.yml" "$work/client-retired.yml" playbooks/system.yml
assert_no_change retire-2
expect_ssh_refused "ancien can no longer log in" ancien "$work/ancien.key"
retired="$(on_server sh -c 'passwd -S ancien; chage -l ancien | grep -i "account expires"; id -nG ancien;
  wc -c < /home/ancien/.ssh/authorized_keys; test -d /home/ancien && echo home-kept')"
expect_contains "ancien is locked, expired, without keys or groups, home kept" "$retired" \
  '^ancien L ' 'Jan 02, 1970' '^ancien$' '^0$' '^home-kept$'

# ------------------------------------------------------------------ tailscale
step "tailscale role without auth key"
if run_playbook tailscale "$work/inventory.yml" "$work/client-tailscale.yml" "$here/role.yml" \
  -e dokopilot_test_role=tailscale; then
  fail "the tailscale role succeeded without auth key"
fi
expect_contains "the tailscale role stops with the French message" "$(tr -s ' \n' ' ' < "$work/tailscale.log")" \
  "aucune clé d'authentification n'est fournie"
expect_contains "tailscaled installed and waiting for a login" "$(on_server tailscale status --json)" \
  '"BackendState": "NeedsLogin"'
grep -q -- "$sudo_password" "$work"/*.log && fail "the sudo password appears in a playbook log"
ok "no secret in the playbook logs"

# ------------------------------------------------------------------ full installation
if [[ "${DP_TEST_SITE:-0}" == 1 ]]; then
  step "Full installation: site.yml (Dokos stack, demo data, backups, proxy)"
  if [[ -n "$apt_proxy" ]]; then
    # Test only: the nested Docker daemon reaches the registries through the relayed proxy.
    on_server_stdin sh -ec 'mkdir -p /etc/systemd/system/docker.service.d
      cat > /etc/systemd/system/docker.service.d/dokopilot-test-proxy.conf
      systemctl daemon-reload && systemctl restart docker' <<EOF
[Service]
Environment=HTTPS_PROXY=$apt_proxy HTTP_PROXY=$apt_proxy NO_PROXY=localhost,127.0.0.1
EOF
  fi
  # The stand-in of the proxy step holds 127.0.0.1:8080, the port of the Dokos frontend.
  on_server docker rm -f dokos-stub >/dev/null
  # Test only: an image build behind this machine's TLS-intercepting proxy needs its CA.
  echo '{}' > "$work/site-overrides.yml"
  if [[ -n "$apt_proxy" ]]; then
    cat > "$work/site-overrides.yml" <<EOF
dokos_build_network: host
dokos_build_args: { HTTPS_PROXY: "$apt_proxy", HTTP_PROXY: "$apt_proxy", NO_PROXY: "localhost,127.0.0.1" }
dokos_build_ca_file: /usr/local/share/ca-certificates/dokopilot-test-extra-ca.crt
EOF
  fi
  must_run site-1 "$work/inventory.yml" "$work/client-retired.yml" playbooks/site.yml -e "@$work/site-overrides.yml"
  must_run site-2 "$work/inventory.yml" "$work/client-retired.yml" playbooks/site.yml -e "@$work/site-overrides.yml"
  assert_no_change site-2
  expect_contains "site.yml prints the French summary" "$(cat "$work/site-2.log")" \
    "Installation terminée pour Atelier Démo" "Dokos : http://$server_ip"

  step "check.yml, everything"
  must_run check-full "$work/inventory.yml" "$work/client-retired.yml" playbooks/check.yml
  if grep -q 'ÉCHEC' "$work/check-full.log"; then
    grep 'ÉCHEC' "$work/check-full.log" >&2
    fail "a check failed after the full installation"
  fi
  ok "check.yml passes after the full installation"
  ping="$(curl --noproxy '*' -s --max-time 20 "http://$server_ip/api/method/ping")"
  expect_contains "Dokos answers through Caddy" "$ping" '"message": ?"pong"'

  step "harness.yml with the real assistant token"
  mkdir -p "$work/home"
  if ! (cd "$repo/ansible" && HOME="$work/home" ANSIBLE_CONFIG="$repo/ansible/ansible.cfg" ANSIBLE_FORCE_COLOR=0 \
    ANSIBLE_SSH_CONTROL_PATH_DIR="$work/cp" ANSIBLE_LOCAL_TEMP="$work/ansible-tmp" \
    "$playbook_bin" -i "$work/inventory.yml" -e "@$repo/clients/demo/client.yml" -e "@$work/client-retired.yml" \
    -e "@$work/secrets.yml" -e "harness_dest=$work/workspace" playbooks/harness.yml) > "$work/harness.log" 2>&1; then
    tail -n 40 "$work/harness.log"
    fail "harness.yml failed"
  fi
  token_file="$work/home/.config/dokopilot/demo/dokos-token"
  [[ "$(stat -c %a "$token_file")" == 600 ]] || fail "the token file is not private (0600)"
  token="$(cat "$token_file")"
  if grep -rqF -- "$token" "$work/workspace"; then
    fail "the assistant token was written into the workspace"
  fi
  ok "workspace rendered, token kept in a private file outside the workspace"
  tools="$(curl --noproxy '*' -s --max-time 30 -H "Authorization: token $token" -H 'Content-Type: application/json' \
    -H 'Accept: application/json' -H 'MCP-Protocol-Version: 2025-11-25' \
    --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
    "http://$server_ip/api/method/dokos_ai.mcp.endpoint")"
  expect_contains "MCP through Caddy with the token rendered for the harness" "$tools" '"doc_list"' '"doc_create"'
  unset token

  step "backup.yml"
  must_run backup "$work/inventory.yml" "$work/client-retired.yml" playbooks/backup.yml
  snapshots="$(on_server sh -c 'set -a; . /etc/dokopilot/restic.env; restic snapshots --compact')"
  expect_contains "a restic snapshot exists" "$snapshots" '[0-9]+ snapshots?'
  # No secret in the playbook logs, nor on the server in the sudo log or the journal.
  sed -n 's/^  [a-z_]*password: "\(.*\)"$/\1/p' "$work/secrets.yml" > "$work/secret-values.txt"
  [[ -s "$work/secret-values.txt" ]] || fail "test setup: no secret values to look for"
  on_server sh -c 'cat /var/log/sudo.log 2>/dev/null; journalctl --no-pager -q -o cat' > "$work/server-logs.txt"
  if grep -qF -f "$work/secret-values.txt" "$work"/*.log "$work/server-logs.txt"; then
    fail "a secret appears in a playbook log, the sudo log or the journal of the server"
  fi
  ok "no secret in the playbook logs, the sudo log or the journal of the server"
fi

step "All system checks passed"
