#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Installer test: install.sh and installer/wizard.py as a person runs them, in Debian 13,
# Ubuntu 24.04 and Fedora containers, up to the validated configuration (bin/dokopilot config
# passes), without installing a server. For each system, as an ordinary account with sudo:
#   1. install.sh --check, run directly and piped into sh, on a bare image;
#   2. install.sh --answers: packages (apt or dnf), clone of this working tree at a branch
#      (a local snapshot stands for GitHub), make setup, then the assistant up to config;
#   3. the files written: client.yml (format 0.2), client.local.yml, the model key file
#      (0600, folder 0700), the secrets stored encrypted, no secret in clear in the client
#      files, the state file, the DokoPilot copy or the logs;
#   4. install.sh --answers again: the existing copy is updated and the assistant resumes,
#      without changing the files.
# Debian runs "everything on this machine" (local mode) with its checks; Ubuntu and Fedora
# drive a remote server (demo with Tailscale; production with backup, mail and SIREN).
# Environment: DP_TEST_DISTROS (default "debian ubuntu fedora"), DP_TEST_KEEP=1 to keep the
# containers and work files, DP_TEST_SUBNET_PREFIX (default 172.30.96, a /24),
# DP_TEST_CA_FILE (extra CA of an HTTPS proxy, default $SSL_CERT_FILE).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
# shellcheck source=tests/installer/lib.sh
. "$here/lib.sh"

distros="${DP_TEST_DISTROS:-debian ubuntu fedora}"
network="dokopilot-installer-test"
subnet_prefix="${DP_TEST_SUBNET_PREFIX:-172.30.96}"
gateway="$subnet_prefix.1"
work="$(mktemp -d)"
containers=()

cleanup() {
  local rc=$?
  cleanup_relay
  if [[ "${DP_TEST_KEEP:-0}" == 1 ]]; then
    echo "Kept: containers ${containers[*]:-none}, network $network, work files in $work"
  else
    for container in "${containers[@]}"; do docker rm -f -v "$container" >/dev/null 2>&1 || true; done
    docker network rm "$network" >/dev/null 2>&1 || true
    rm -rf "$work"
  fi
  exit "$rc"
}
trap cleanup EXIT

command -v docker >/dev/null || fail "docker is required"

# Secrets of the test: random, never written into a file the installer reads.
model_key="sk-test-$(random_token)"
ts_key="tskey-auth-test$(random_token)"
mail_password="mail-$(random_token)"
s3_id="AKTEST$(random_token)"
s3_secret="s3-$(random_token)"
printf '%s\n' "$model_key" "$ts_key" "$mail_password" "$s3_id" "$s3_secret" > "$work/secret-values.txt"
secret_env=(-e "DP_TEST_MODEL_KEY=$model_key" -e "DP_TEST_TS_KEY=$ts_key" -e "DP_TEST_MAIL_PASSWORD=$mail_password"
  -e "DP_TEST_S3_ID=$s3_id" -e "DP_TEST_S3_SECRET=$s3_secret")
# The SIREN of the production case: 9 digits with a valid check digit (Luhn).
siren=732829320

step "Snapshot of the working tree"
make_snapshot "$work/snapshot"
ok "snapshot committed on branch installer-test ($(git -C "$work/snapshot" rev-parse --short HEAD))"

docker network rm "$network" >/dev/null 2>&1 || true
docker network create --subnet "$subnet_prefix.0/24" --gateway "$gateway" "$network" >/dev/null
start_relay "$gateway"
mapfile -t proxy_opts < <(proxy_env)

# ------------------------------------------------------------------ answers

write_answers() {
  case "$1" in
  debian)
    cat <<'EOF'
stop_after: config
where: local
profile: demo
client_id: atelier-local
harness: opencode
models:
  provider: mistral
  api_key_env: DP_TEST_MODEL_KEY
people:
  - first_name: Camille
    last_name: Martin
    email: camille.martin@atelier-demo.test
  - first_name: Lucas
    last_name: Bernard
    email: lucas.bernard@atelier-demo.test
    assistant: non
network: lan
import_path: ~/coffre
EOF
    ;;
  ubuntu)
    cat <<'EOF'
stop_after: config
where: distant
server:
  address: 192.0.2.10
  bootstrap_user: ubuntu
ssh_key_create: oui
profile: demo
client_id: atelier-ubuntu
harness: les-deux
models:
  provider: anthropic
  api_key_file: ~/cle-modele.txt
claude_code_auth: cle-api
people:
  - first_name: Camille
    last_name: Martin
    email: camille.martin@atelier-demo.test
network: tailscale
tailscale_auth_key_env: DP_TEST_TS_KEY
EOF
    ;;
  fedora)
    cat <<EOF
stop_after: config
where: "2"
server:
  address: serveur.menuiserie-test.fr
  ssh_port: 2222
  bootstrap_user: root
profile: production
client_id: menuiserie-test
harness: opencode
models:
  provider: openrouter
  bureau: exemple/modele-bureau
  atelier: exemple/modele-atelier
  api_key_env: DP_TEST_MODEL_KEY
company:
  name: "Menuiserie Test"
  siren: "$siren"
people:
  - first_name: Élodie
    last_name: Durand
    email: elodie.durand@menuiserie-test.fr
tailscale_auth_key_env: DP_TEST_TS_KEY
backup:
  repository: "s3:https://s3.fr-par.scw.cloud/compartiment-test/dokopilot"
  s3_access_key_id_env: DP_TEST_S3_ID
  s3_secret_access_key_env: DP_TEST_S3_SECRET
mail:
  enabled: oui
  imap_host: imap.menuiserie-test.fr
  username: elodie.durand@menuiserie-test.fr
  password_env: DP_TEST_MAIL_PASSWORD
EOF
    ;;
  esac
}

write_expect() {
  case "$1" in
  debian)
    cat <<'EOF'
{"client": "atelier-local", "profile": "demo", "local": true, "maintenance": "camille", "provider": "mistral",
 "bureau": "mistral/mistral-large-4", "small": "", "harness": ["opencode"], "network": "lan",
 "people": ["camille", "lucas"], "import_path": "/home/camille/coffre", "model_key_env": "DP_TEST_MODEL_KEY",
 "secret_envs": ["DP_TEST_MODEL_KEY"]}
EOF
    ;;
  ubuntu)
    cat <<'EOF'
{"client": "atelier-ubuntu", "profile": "demo", "local": false, "maintenance": "camille", "provider": "anthropic",
 "bureau": "anthropic/claude-sonnet-5-5", "small": "anthropic/claude-haiku-5-5", "harness": ["opencode", "claude-code"],
 "network": "tailscale", "people": ["camille"], "model_key_env": "DP_TEST_MODEL_KEY",
 "stored_secrets": {"tailscale_auth_key": "DP_TEST_TS_KEY"}, "secret_envs": ["DP_TEST_MODEL_KEY", "DP_TEST_TS_KEY"]}
EOF
    ;;
  fedora)
    cat <<'EOF'
{"client": "menuiserie-test", "profile": "production", "local": false, "maintenance": "camille", "provider": "openrouter",
 "bureau": "openrouter/exemple/modele-bureau", "small": "", "harness": ["opencode"], "network": "tailscale",
 "people": ["elodie"], "model_key_env": "DP_TEST_MODEL_KEY",
 "stored_secrets": {"tailscale_auth_key": "DP_TEST_TS_KEY", "restic_env.AWS_ACCESS_KEY_ID": "DP_TEST_S3_ID",
                    "restic_env.AWS_SECRET_ACCESS_KEY": "DP_TEST_S3_SECRET"},
 "private_files": {"mail-password": "DP_TEST_MAIL_PASSWORD"},
 "secret_envs": ["DP_TEST_MODEL_KEY", "DP_TEST_TS_KEY", "DP_TEST_MAIL_PASSWORD", "DP_TEST_S3_ID", "DP_TEST_S3_SECRET"]}
EOF
    ;;
  esac
}

# ------------------------------------------------------------------ one system

# as_user CONTAINER ARGS...: a command as the test account, with the test environment
# (as_user_stdin: the same, with this script's standard input forwarded).
as_user_stdin() {
  local container="$1"
  shift
  docker exec -i -u camille -w /home/camille -e HOME=/home/camille -e USER=camille -e LANG=C.UTF-8 \
    -e DOKOPILOT_REPO_URL=file:///srv/dokopilot "${proxy_opts[@]}" "${secret_env[@]}" "$container" "$@"
}
as_user() {
  local container="$1"
  shift
  docker exec -u camille -w /home/camille -e HOME=/home/camille -e USER=camille -e LANG=C.UTF-8 \
    -e DOKOPILOT_REPO_URL=file:///srv/dokopilot "${proxy_opts[@]}" "${secret_env[@]}" "$container" "$@"
}

test_distro() {
  local distro="$1" image container="dokopilot-installer-$1" log
  case "$distro" in
  debian) image=debian:trixie ;;
  ubuntu) image=ubuntu:24.04 ;;
  fedora) image="${DP_TEST_FEDORA_IMAGE:-quay.io/fedora/fedora:44}" ;;
  *) fail "unknown distribution $distro" ;;
  esac
  step "$distro ($image): bare system with an ordinary account"
  docker rm -f -v "$container" >/dev/null 2>&1 || true
  containers+=("$container")
  docker run -d --name "$container" --hostname "dp-$distro" --network "$network" "$image" sleep infinity >/dev/null
  install_ca "$container"
  compensate_clock "$container"
  # Test setup, as root: sudo and an account allowed to use it without a password (as on a
  # cloud image), and, on Fedora, dnf through the relayed proxy. Everything else is the
  # installer's job.
  if [[ "$distro" == fedora ]]; then
    if [[ -n "$proxy_url" ]]; then
      docker exec "$container" sh -ec "echo 'proxy=$proxy_url' >> /etc/dnf/dnf.conf"
      if [[ -n "$ca_file" ]]; then
        docker exec "$container" sh -ec 'cp /usr/local/share/ca-certificates/dokopilot-test-ca.crt /etc/pki/ca-trust/source/anchors/ && update-ca-trust'
      fi
    fi
    docker exec "$container" sh -ec 'missing=""; command -v sudo >/dev/null || missing="sudo"
      command -v useradd >/dev/null || missing="$missing shadow-utils"
      if [ -n "$missing" ]; then dnf install -y -q $missing >/dev/null; fi'
  else
    docker exec "$container" sh -ec 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq >/dev/null; apt-get install -y -qq sudo >/dev/null'
  fi
  docker exec "$container" sh -ec 'useradd --create-home --shell /bin/bash --comment "Camille Martin" camille
    echo "camille ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-test-camille; chmod 0440 /etc/sudoers.d/90-test-camille'
  docker cp "$work/snapshot" "$container:/srv/dokopilot" >/dev/null
  docker exec "$container" sh -ec 'chown -R camille:camille /srv/dokopilot
    cp /srv/dokopilot/install.sh /home/camille/install.sh; chown camille:camille /home/camille/install.sh'
  write_answers "$distro" > "$work/$distro-answers.yml"
  write_expect "$distro" > "$work/$distro-expect.json"
  docker cp "$work/$distro-answers.yml" "$container:/home/camille/reponses.yml" >/dev/null
  docker cp "$work/$distro-expect.json" "$container:/home/camille/expect.json" >/dev/null
  docker cp "$here/check_result.py" "$container:/home/camille/check_result.py" >/dev/null
  docker exec "$container" chown camille:camille /home/camille/reponses.yml /home/camille/expect.json /home/camille/check_result.py
  case "$distro" in
  debian) as_user "$container" sh -ec 'mkdir -p ~/coffre && echo "# Note importée" > ~/coffre/note.md' ;;
  ubuntu)
    # shellcheck disable=SC2016 # expanded inside the container
    as_user "$container" sh -ec 'umask 077; printf "%s\n" "$DP_TEST_MODEL_KEY" > ~/cle-modele.txt'
    ;;
  fedora)
    # An SSH key made beforehand on this computer (the assistant uses it as it is).
    ssh-keygen -q -t ed25519 -N '' -C camille@poste -f "$work/fedora-id_ed25519"
    docker exec "$container" install -d -m 700 -o camille -g camille /home/camille/.ssh
    docker cp "$work/fedora-id_ed25519" "$container:/home/camille/.ssh/id_ed25519" >/dev/null
    docker cp "$work/fedora-id_ed25519.pub" "$container:/home/camille/.ssh/id_ed25519.pub" >/dev/null
    docker exec "$container" sh -ec 'chown camille:camille /home/camille/.ssh/id_ed25519*; chmod 600 /home/camille/.ssh/id_ed25519'
    ;;
  esac
  ok "account camille ready (sudo without password, as on a cloud image)"

  step "$distro: install.sh --check"
  log="$work/$distro-check.log"
  as_user "$container" sh install.sh --check > "$log" 2>&1 || { cat "$log"; fail "$distro: install.sh --check failed"; }
  expect_contains "$distro: --check reports the system, the packages to install and the verdict" "$(cat "$log")" \
    '✓ Système' 'Paquets à installer : .*git' 'Cette machine convient comme poste'
  if [[ "$distro" == debian ]]; then
    as_user_stdin "$container" env DOKOPILOT_LOCAL_MIN_DISK_GB=100000 sh -s -- --check < "$work/snapshot/install.sh" > "$work/$distro-check-piped.log" 2>&1 ||
      { cat "$work/$distro-check-piped.log"; fail "$distro: piped --check failed"; }
    expect_contains "$distro: piped into sh, --check works and refuses local mode without the disk space" \
      "$(cat "$work/$distro-check-piped.log")" '« Tout sur cette machine » : impossible ici \(.*Go libres dans /var, il en faut au moins 100000'
  else
    expect_contains "$distro: local mode is refused off Debian 13" "$(cat "$log")" '« Tout sur cette machine » : impossible ici \(le système n.est pas Debian 13'
  fi

  step "$distro: install.sh --answers (packages, clone, make setup, assistant up to config)"
  log="$work/$distro-install.log"
  if ! as_user "$container" env DOKOPILOT_LOCAL_MIN_DISK_GB=1 sh install.sh --version installer-test --answers reponses.yml > "$log" 2>&1; then
    tail -n 60 "$log"
    fail "$distro: install.sh --answers failed (end of the log above)"
  fi
  expect_contains "$distro: packages installed, DokoPilot cloned, assistant run, configuration validated" "$(cat "$log")" \
    '✓ Installés : ' 'DokoPilot installer-test \(' 'Étape 6 sur 6' 'failed=0' "Configuration de « [a-z-]+ » validée"
  if ! as_user "$container" DokoPilotV2/.venv/bin/python check_result.py expect.json > "$work/$distro-check-result.log" 2>&1; then
    cat "$work/$distro-check-result.log"
    fail "$distro: the files written by the assistant are not as expected"
  fi
  sed 's/^/      /' "$work/$distro-check-result.log"
  ok "$distro: client files, key file, secrets and state as expected"
  local client
  client="$(sed -n 's/^client_id: //p' "$work/$distro-answers.yml")"
  # Through the .venv Python, as the assistant runs it: run directly, bin/dokopilot needs a
  # system PyYAML (its re-exec into .venv compares resolved paths, the same /usr/bin/python3.x).
  if ! as_user "$container" sh -ec "cd DokoPilotV2 && .venv/bin/python bin/dokopilot config $client" > "$work/$distro-config.log" 2>&1; then
    tail -n 30 "$work/$distro-config.log"
    fail "$distro: bin/dokopilot config $client failed after the assistant"
  fi
  ok "$distro: bin/dokopilot config $client passes"

  step "$distro: install.sh --answers again (existing copy, the assistant resumes)"
  local before after
  before="$(as_user "$container" sh -c "md5sum DokoPilotV2/clients/$client/client.yml DokoPilotV2/clients/$client/client.local.yml .config/dokopilot/$client/*")"
  log="$work/$distro-resume.log"
  if ! as_user "$container" env DOKOPILOT_LOCAL_MIN_DISK_GB=1 sh install.sh --version installer-test --answers reponses.yml > "$log" 2>&1; then
    tail -n 40 "$log"
    fail "$distro: second install.sh --answers failed"
  fi
  expect_contains "$distro: the existing copy is updated and the assistant resumes" "$(cat "$log")" \
    'Copie existante dans .*passage à la version installer-test' 'Rien à installer' "Reprise de l'installation de « $client »" \
    'Étape 5 sur 6 — .* : déjà faite' "Configuration de « $client » validée"
  grep -q 'bin/dokopilot init' "$log" && fail "$distro: the resumed run ran init again"
  after="$(as_user "$container" sh -c "md5sum DokoPilotV2/clients/$client/client.yml DokoPilotV2/clients/$client/client.local.yml .config/dokopilot/$client/*")"
  [[ "$before" == "$after" ]] || fail "$distro: the resumed run changed the client files or the key file"
  ok "$distro: resumed run changed nothing"

  if [[ "$distro" == debian ]]; then
    step "$distro: answers the assistant must refuse"
    # shellcheck disable=SC2016 # expanded inside the container
    as_user "$container" sh -ec 'cd DokoPilotV2
      printf "where: local\nprofile: demo\nclient_id: demo\n" > /tmp/bad-id.yml
      printf "where: distant\nserver: {address: 192.0.2.1}\nprofile: demo\nclient_id: essai-refus\nharness: opencode\nmodels: {provider: mistral, api_key: %s}\n" "$DP_TEST_MODEL_KEY" > /tmp/bad-secret.yml' >/dev/null
    local output rc
    rc=0
    output="$(as_user "$container" sh -c 'cd DokoPilotV2 && DOKOPILOT_LOCAL_MIN_DISK_GB=1 .venv/bin/python installer/wizard.py --answers /tmp/bad-id.yml' 2>&1)" || rc=$?
    [[ "$rc" == 2 ]] || fail "$distro: client_id demo accepted (code $rc)"
    expect_contains "$distro: the versioned example id « demo » is refused" "$output" 'exemple versionné du dépôt'
    rc=0
    output="$(as_user "$container" sh -c 'cd DokoPilotV2 && .venv/bin/python installer/wizard.py --answers /tmp/bad-secret.yml' 2>&1)" || rc=$?
    [[ "$rc" == 2 ]] || fail "$distro: a secret written in the answers file was accepted (code $rc)"
    expect_contains "$distro: a secret written in the answers file is refused" "$output" 'un secret ne s.écrit pas dans le fichier de réponses'
    printf '%s\n' "$output" > "$work/$distro-refusals.log"
    as_user "$container" rm -f /tmp/bad-id.yml /tmp/bad-secret.yml
  fi

  if grep -qF -f "$work/secret-values.txt" "$work/$distro"-*.log; then
    fail "$distro: a secret appears in a log of the installer"
  fi
  ok "$distro: no secret in the logs of install.sh, the assistant and bin/dokopilot"
}

for distro in $distros; do
  test_distro "$distro"
done

step "All installer checks passed ($distros)"
