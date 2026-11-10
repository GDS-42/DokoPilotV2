#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# End-to-end test of version 0.2 (docs/plan-v0.2.md C-7, `make test-e2e`): the complete
# installation "everything on this machine" by install.sh --answers, as a person runs it on a
# fresh Debian 13 VM. A Debian 13 container with systemd as PID 1 plays the machine; camille
# (sudo group, password, password-less cloud-init rule) runs the script from her terminal and
# types her sudo password at each « BECOME password » prompt (tests/installer/pty_answer.py).
#   1. install.sh --answers, no stop: configuration, server (Dokos, Forgejo, context), harness
#      for opencode and Claude Code, checks, summary;
#   2. Dokos and Forgejo answer on localhost and on the local network, the Forgejo accounts and
#      context repositories exist, MCP answers with the assistant's token;
#   3. the workspace ~/DokoPilot/<client> holds the copies of entreprise, direction and perso,
#      with no token or password in it; « bin/dokopilot sync » pushes a commit of the person;
#   4. bin/dokopilot install and harness again: nothing changes; check and backup pass;
#   5. no secret in the logs, the sudo log or the journal of the machine.
# Needs Docker, about 6 GB of memory for the container, 12 GB of free disk and one hour.
# Environment: DP_TEST_KEEP=1 to keep the container and the work files,
# DP_TEST_SUBNET_PREFIX (default 172.30.96, a /24), DP_TEST_CA_FILE (extra CA of an HTTPS
# proxy, default $SSL_CERT_FILE), DP_TEST_MEMORY (default 6g).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
# shellcheck source=tests/installer/lib.sh
. "$here/lib.sh"

image="dokopilot-installer-local:trixie"
container="dokopilot-installer-e2e"
network="dokopilot-installer-e2e"
subnet_prefix="${DP_TEST_SUBNET_PREFIX:-172.30.96}"
gateway="$subnet_prefix.1"
machine_ip="$subnet_prefix.10"
client=atelier-e2e
work="$(mktemp -d)"
chmod 700 "$work"
preload_pid=""

cleanup() {
  local rc=$?
  cleanup_relay
  if [[ -n "$preload_pid" ]]; then
    kill "$preload_pid" 2>/dev/null || true
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

command -v docker >/dev/null || fail "docker is required"
command -v ssh-keygen >/dev/null || fail "ssh-keygen is required"
command -v curl >/dev/null || fail "curl is required"

on_machine() { docker exec "$container" "$@"; }
on_machine_stdin() { docker exec -i "$container" "$@"; }

camille_password="$(random_token)"
model_key="sk-test-$(random_token)"
home=/home/camille
workspace="$home/DokoPilot/$client"
config_dir="$home/.config/dokopilot/$client"

# camille_env: what her login shell would have, plus the test-only settings (proxy, smaller
# disk threshold, role variables of the sandbox, no colours in the logs).
camille_env() {
  printf '%s\n' -u camille -w "$home" -e "HOME=$home" -e USER=camille -e LANG=C.UTF-8 -e ANSIBLE_NOCOLOR=1 \
    -e ANSIBLE_FORCE_COLOR=0 -e DOKOPILOT_REPO_URL=file:///srv/dokopilot -e DOKOPILOT_LOCAL_MIN_DISK_GB="${DP_TEST_MIN_DISK_GB:-10}" \
    -e "DP_TEST_MODEL_KEY=$model_key" -e "DOKOPILOT_TEST_EXTRA_VARS=$home/t/extra-vars.yml"
  printf '%s\n' "${proxy_opts[@]}"
}
as_camille() {
  local opts
  mapfile -t opts < <(camille_env)
  docker exec "${opts[@]}" "$container" "$@"
}

# at_terminal NAME COMMAND...: run by camille in a terminal; every « BECOME password » prompt
# gets her password, as she would type it. Output in $work/NAME.log (CR removed).
at_terminal() {
  local name="$1" opts rc=0
  shift
  mapfile -t opts < <(camille_env)
  python3 "$here/pty_answer.py" "$work/become" "$work/$name.raw" -- \
    docker exec -it "${opts[@]}" "$container" "$@" || rc=$?
  tr -d '\r' < "$work/$name.raw" | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' > "$work/$name.log"
  rm -f "$work/$name.raw"
  return "$rc"
}

must_terminal() {
  local name="$1"
  if ! at_terminal "$@"; then
    tail -n 80 "$work/$name.log"
    fail "'$name' failed (end of its output above)"
  fi
  ok "'$name' succeeded"
}

assert_no_change() {
  local name="$1" changed
  # Every host of the recap (the harness play also runs on localhost, the workstation).
  changed="$(sed -n 's/^[A-Za-z0-9._-]*[[:space:]]*: ok=[0-9]*[[:space:]]*changed=\([0-9][0-9]*\).*/\1/p' "$work/$name.log" \
    | sort -u | tr '\n' ' ' | sed 's/ $//')"
  if [[ "$changed" != 0 ]]; then
    awk '/^TASK \[/ { task = $0 } /^changed: / { print task }' "$work/$name.log" >&2
    fail "'$name' is not idempotent: changed=${changed:-?} (changed tasks above)"
  fi
  ok "'$name' changed nothing (idempotent)"
}

no_echec() {
  local name="$1"
  if grep -q 'ÉCHEC' "$work/$name.log"; then
    grep 'ÉCHEC' "$work/$name.log" >&2
    fail "a check failed in '$name'"
  fi
}

# preload_images: test only. Docker Hub and Codeberg rate-limit shared addresses; once the
# docker role has started the machine's Docker daemon, give it the database, cache and
# Forgejo images of this machine when it has them (same digests as the role defaults). A
# loaded image has no name: tag it, then it resolves by its digest as the roles ask.
preload_images() {
  local ref digest
  for _ in $(seq 900); do
    on_machine docker info >/dev/null 2>&1 && break
    sleep 4
  done
  while IFS= read -r ref; do
    digest="${ref##*@}"
    docker image inspect "${ref%%:*}@$digest" >/dev/null 2>&1 || continue
    if docker save "${ref%%:*}@$digest" | on_machine_stdin docker load --quiet >/dev/null 2>&1 \
      && on_machine docker tag "$digest" "${ref%@*}" >/dev/null 2>&1; then
      echo "preloaded $ref"
    else
      echo "could not preload $ref (the role pulls it)"
    fi
  done < <(sed -n 's/^dokos_\(mariadb\|valkey\)_image: //p' "$repo/ansible/roles/dokos/defaults/main.yml"
    sed -n 's/^forgejo_image: //p' "$repo/ansible/roles/forgejo/defaults/main.yml")
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
docker run -d --name "$container" --hostname dp-e2e-test --network "$network" --ip "$machine_ip" \
  --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock \
  -v /var/lib/docker -v /var/lib/containerd --memory "${DP_TEST_MEMORY:-6g}" "$image" >/dev/null
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
compensate_clock "$container"

printf 'camille:%s\n' "$camille_password" | on_machine_stdin chpasswd
ssh-keygen -q -t ed25519 -N '' -C camille@portable -f "$work/laptop.key"
on_machine install -d -m 700 -o camille -g camille "$home/.ssh"
on_machine_stdin sh -ec "cat > $home/.ssh/authorized_keys; chown camille:camille $home/.ssh/authorized_keys
  chmod 600 $home/.ssh/authorized_keys" < "$work/laptop.key.pub"
(umask 077 && printf '%s\n' "$camille_password" > "$work/become")
ok "camille: password set, laptop key authorized"

step "Test-only settings of the sandbox"
# The nested Docker daemon reaches the registries through the relayed proxy: a drop-in that
# exists before the docker role installs the daemon (systemd reads it on the first start).
if [[ -n "$proxy_url" ]]; then
  on_machine_stdin sh -ec 'mkdir -p /etc/systemd/system/docker.service.d
    cat > /etc/systemd/system/docker.service.d/dokopilot-test-proxy.conf' <<EOF
[Service]
Environment=HTTPS_PROXY=$proxy_url HTTP_PROXY=$proxy_url NO_PROXY=localhost,127.0.0.1
EOF
fi
# Role variables the client files cannot carry: no swap file in a container, and an image
# build through the proxy with its CA (bin/dokopilot reads them from DOKOPILOT_TEST_EXTRA_VARS).
{
  echo "base_swap_size_mb: 0"
  if [[ -n "$proxy_url" ]]; then
    echo "dokos_build_network: host"
    echo "dokos_build_args: { HTTPS_PROXY: \"$proxy_url\", HTTP_PROXY: \"$proxy_url\", NO_PROXY: \"localhost,127.0.0.1\" }"
    [[ -n "$ca_file" ]] && echo "dokos_build_ca_file: /usr/local/share/ca-certificates/dokopilot-test-ca.crt"
  fi
} > "$work/extra-vars.yml"
on_machine install -d -m 700 -o camille -g camille "$home/t"
docker cp "$work/extra-vars.yml" "$container:$home/t/extra-vars.yml" >/dev/null
on_machine chown camille:camille "$home/t/extra-vars.yml"
ok "dockerd proxy drop-in and role variables in place"

step "Working tree snapshot and answers"
make_snapshot "$work/snapshot"
docker cp "$work/snapshot" "$container:/srv/dokopilot" >/dev/null
cat > "$work/answers.yml" <<EOF
where: local
profile: demo
client_id: $client
harness: les-deux
models:
  provider: mistral
  api_key_env: DP_TEST_MODEL_KEY
claude_code_auth: cle-api
company:
  name: "Atelier E2E"
  abbr: AE
people:
  - first_name: Camille
    last_name: Martin
    email: camille.martin@atelier-e2e.test
  - first_name: Lucas
    last_name: Bernard
    email: lucas.bernard@atelier-e2e.test
network: lan
EOF
docker cp "$work/answers.yml" "$container:$home/reponses.yml" >/dev/null
on_machine sh -ec "chown -R camille:camille /srv/dokopilot $home/reponses.yml
  cp /srv/dokopilot/install.sh $home/install.sh; chown camille:camille $home/install.sh"
ok "snapshot $(git -C "$work/snapshot" rev-parse --short HEAD) and answers in place"

# ------------------------------------------------------------------ installation
step "install.sh --answers in local mode, to the end (30 to 60 minutes)"
preload_images > "$work/preload.log" 2>&1 &
preload_pid=$!
started=$SECONDS
must_terminal install sh install.sh --version installer-test --answers reponses.yml
ok "installation took $(((SECONDS - started) / 60)) min; $(tr '\n' ';' < "$work/preload.log")"
no_echec install
expect_contains "install.sh: every step ran, checks passed, French summary printed" "$(cat "$work/install.log")" \
  "Votre compte « camille » devient le compte de maintenance" "bin/dokopilot install $client --local -K" \
  "bin/dokopilot harness $client --local -K" "bin/dokopilot check $client --local -K" \
  "Installation terminée pour « Atelier E2E » \\($client\\)" 'Dokos +http://localhost' 'Forgejo +http://localhost:3000/' \
  "Espace +~/DokoPilot/$client" '· opencode : le harnais par défaut' '· Claude Code : le harnais en option' \
  'sudo a oublié votre mot de passe \(sudo -K\)' "[~]/DokoPilotV2/bin/dokopilot sync $client"
# sudo -K removed every cached credential of camille (the agent would otherwise inherit them).
if on_machine test -e /run/sudo/ts/camille; then
  fail "sudo still holds cached credentials of camille after the installation (/run/sudo/ts/camille)"
fi
ok "sudo holds no cached credential of camille after the installation (sudo -K)"
grep -q 'BECOME password' "$work/install.log" || fail "Ansible never asked for the sudo password"

# ------------------------------------------------------------------ server
step "Dokos and Forgejo"
expect_contains "Dokos answers on localhost" "$(on_machine curl -s --max-time 20 http://localhost/api/method/ping)" \
  '"message": ?"pong"'
expect_contains "Dokos answers on the local network" \
  "$(curl --noproxy '*' -s --max-time 20 "http://$machine_ip/api/method/ping")" '"message": ?"pong"'
# Forgejo asks a sign-in for everything but its health check (REQUIRE_SIGNIN_VIEW).
health="$(on_machine curl -s --max-time 20 http://localhost:3000/api/healthz | tr -d ' \n'; echo
  curl --noproxy '*' -s --max-time 20 "http://$machine_ip:3000/api/healthz" | tr -d ' \n')"
[[ "$(grep -c '^{"status":"pass"' <<< "$health")" == 2 ]] || fail "Forgejo is not healthy on localhost:3000 and on the local network"
expect_contains "Forgejo answers on localhost:3000 and on the local network, and asks a sign-in" \
  "$(curl --noproxy '*' -s --max-time 20 "http://$machine_ip:3000/api/v1/repos/search")" 'Only signed in user is allowed'
on_machine sh -c 'ss -Htln' | awk '{ print $4 }' | grep -q '^127\.0\.0\.1:3001$' || fail "Forgejo does not listen on 127.0.0.1:3001"
ok "Forgejo itself listens on 127.0.0.1:3001 only"

as_camille sh -ec "umask 077; age --decrypt --identity ~/.config/dokopilot/age-identity.txt \
  DokoPilotV2/clients/$client/secrets.yml.age > ~/t/secrets.yml"
admin_password="$(as_camille DokoPilotV2/.venv/bin/python -c 'import sys, yaml
print(yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["secrets"]["forgejo_admin_password"])' "$home/t/secrets.yml")"
[[ -n "$admin_password" ]] || fail "test setup: no forgejo_admin_password in the secrets"
(umask 077 && printf 'machine %s login dokopilot-admin password %s\n' "$machine_ip" "$admin_password" > "$work/netrc")
forgejo_api() { curl --noproxy '*' -s --max-time 20 --netrc-file "$work/netrc" "http://$machine_ip:3000/api/v1/$1"; }
repos="$(forgejo_api "orgs/$client/repos?limit=50" | python3 -c 'import json,sys; print("\n".join(sorted(r["name"] for r in json.load(sys.stdin))))')"
expect_contains "context repositories of $client" "$repos" '^direction$' '^entreprise$' '^perso-camille$' '^perso-lucas$'
users="$(forgejo_api 'admin/users?limit=50' | python3 -c 'import json,sys; print("\n".join(sorted(u["login"] for u in json.load(sys.stdin))))')"
expect_contains "Forgejo accounts of the people and of their assistants" "$users" '^camille$' '^lucas$' '^assistant-camille$' \
  '^assistant-lucas$'
tree="$(forgejo_api "repos/$client/entreprise/contents/" | python3 -c 'import json,sys; print("\n".join(sorted(e["name"] for e in json.load(sys.stdin))))')"
expect_contains "the entreprise repository holds the pre-split tree and the rules" "$tree" \
  '^00-Boite-de-reception$' '^50-Fonctions$' '^90-Modeles$' '^AGENTS\.md$' '^CLAUDE\.md$'

step "Dokos MCP with the assistant's token"
token_file="$config_dir/dokos-token"
[[ "$(on_machine stat -c '%a %U' "$token_file")" == "600 camille" ]] || fail "the Dokos token file is not private to camille"
# shellcheck disable=SC2016 # expanded inside the container
tools="$(as_camille sh -c 'curl -s --noproxy "*" --max-time 30 -H "Authorization: token $(cat "$1")" \
  -H "Content-Type: application/json" -H "Accept: application/json" -H "MCP-Protocol-Version: 2025-11-25" \
  --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}" \
  http://localhost/api/method/dokos_ai.mcp.endpoint' mcp "$token_file")"
expect_contains "MCP answers on localhost with the token kept for the harness" "$tools" '"doc_list"'

# ------------------------------------------------------------------ workstation
step "Workspace of camille"
for path in AGENTS.md CLAUDE.md 00-Boite-de-reception 50-Fonctions opencode.json .mcp.json direction/.git perso/.git; do
  as_camille test -e "$workspace/$path" || fail "$workspace/$path is missing"
done
ok "workspace: entreprise with its rules, direction/ and perso/ copies, opencode and Claude Code files"
for copy in "" /direction /perso; do
  url="$(as_camille git -C "$workspace$copy" remote get-url origin)"
  [[ "$url" == *@* ]] && fail "the remote of $workspace$copy carries credentials"
  as_camille git -C "$workspace$copy" ls-remote origin HEAD >/dev/null || fail "git cannot reach the remote of $workspace$copy"
done
ok "the three copies reach Forgejo through the credential file, with no credential in their URLs"
for file in dokos-token git-credentials forgejo-password model-api-key; do
  [[ "$(on_machine stat -c '%a' "$config_dir/$file")" == 600 ]] || fail "$config_dir/$file is not 0600"
done
[[ "$(as_camille cat "$config_dir/model-api-key")" == "$model_key" ]] || fail "the model key file does not hold the key given"
ok "token, Git credentials, Forgejo password and model key are private files outside the workspace"
# Every credential of this machine, to look for in the workspace and in the logs.
# shellcheck disable=SC2016 # expanded inside the container
as_camille sh -ec 'cat "$1/dokos-token"; echo; cat "$1/forgejo-password"; echo; cat "$1/model-api-key"; echo
  sed -n "s#^[a-z]*://[^:]*:\([^@]*\)@.*#\1#p" "$1/git-credentials"
  DokoPilotV2/.venv/bin/python -c "
import sys, yaml
def walk(v):
    if isinstance(v, dict):
        for x in v.values(): yield from walk(x)
    elif isinstance(v, list):
        for x in v: yield from walk(x)
    elif isinstance(v, str) and len(v) >= 12:
        yield v
print(\"\n\".join(walk(yaml.safe_load(open(sys.argv[1], encoding=\"utf-8\")))))
" ~/t/secrets.yml' creds "$config_dir" > "$work/secret-values.txt"
printf '%s\n' "$camille_password" >> "$work/secret-values.txt"
sed -i '/^.\{0,11\}$/d' "$work/secret-values.txt"
(($(wc -l < "$work/secret-values.txt") >= 6)) || fail "test setup: too few credential values to look for"
docker cp "$work/secret-values.txt" "$container:$home/t/secret-values.txt" >/dev/null
on_machine chown camille:camille "$home/t/secret-values.txt"
if as_camille grep -rqF -f "$home/t/secret-values.txt" "$workspace"; then
  fail "a token, a password or the model key is written in the workspace"
fi
ok "no token, password or key in the workspace (Git history included)"

step "bin/dokopilot sync pushes the work of the person"
as_camille sh -ec "cd $workspace && printf 'Note de test.\n' > 00-Boite-de-reception/note-e2e.md && git add 00-Boite-de-reception/note-e2e.md \
  && git commit -q -m 'Note de test'"
must_terminal sync DokoPilotV2/.venv/bin/python DokoPilotV2/bin/dokopilot sync "$client" --yes
expect_contains "sync shows what it sends" "$(cat "$work/sync.log")" \
  '« entreprise » : 1 enregistrement\(s\) à envoyer' 'note-e2e\.md \| 1 \+' '✓ entreprise : 1 envoyé\(s\)'
local_head="$(as_camille git -C "$workspace" rev-parse HEAD)"
remote_head="$(as_camille git -C "$workspace" ls-remote origin HEAD | cut -f1)"
[[ "$local_head" == "$remote_head" ]] || fail "sync did not push: local $local_head, Forgejo $remote_head"
ok "the commit of camille is on Forgejo"

# ------------------------------------------------------------------ second runs
step "bin/dokopilot install again: no change expected"
must_terminal install-2 DokoPilotV2/.venv/bin/python DokoPilotV2/bin/dokopilot install "$client" --local -K
assert_no_change install-2
step "bin/dokopilot harness again: no change expected"
must_terminal harness-2 DokoPilotV2/.venv/bin/python DokoPilotV2/bin/dokopilot harness "$client" --local -K
assert_no_change harness-2
step "bin/dokopilot check and backup"
must_terminal check DokoPilotV2/.venv/bin/python DokoPilotV2/bin/dokopilot check "$client" --local -K
no_echec check
must_terminal backup DokoPilotV2/.venv/bin/python DokoPilotV2/bin/dokopilot backup "$client" --local -K
expect_contains "a restic snapshot exists" "$(on_machine sh -c 'set -a; . /etc/dokopilot/restic.env; restic snapshots --compact')" \
  '[0-9]+ snapshots?'

# ------------------------------------------------------------------ secrets
step "No secret in the logs"
on_machine sh -c 'cat /var/log/sudo.log 2>/dev/null; journalctl --no-pager -q -o cat' > "$work/machine-logs.txt"
if grep -qF -f "$work/secret-values.txt" "$work"/*.log "$work/machine-logs.txt"; then
  fail "a secret appears in the terminal output, the sudo log or the journal of the machine"
fi
ok "no secret in the terminal output, the sudo log or the journal"

step "All end-to-end checks passed"
