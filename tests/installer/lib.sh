#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Helpers shared by tests/installer/run.sh and tests/installer/local-mode.sh (sourced).
# The caller sets: repo, work, and calls cleanup_relay from its EXIT trap.

step() { printf '\n=== %s\n' "$*"; }
ok() { printf 'ok    %s\n' "$*"; }
fail() {
  printf 'FAIL  %s\n' "$*" >&2
  exit 1
}

# expect_contains LABEL TEXT PATTERN...: every extended regex must match TEXT.
expect_contains() {
  local label="$1" text="$2" pattern
  shift 2
  for pattern in "$@"; do
    if ! grep -Eq -- "$pattern" <<< "$text"; then
      printf '%s\n' "$text" | tail -n 40 >&2
      fail "$label: '$pattern' not found (end of the output above)"
    fi
  done
  ok "$label"
}

random_token() { head -c 24 /dev/urandom | base64 | tr -d '/+='; }

# make_snapshot DIR: the working tree as it is (tracked and new files, without ignored ones),
# committed in a throwaway repository on branch installer-test. install.sh clones it as it
# would clone GitHub at a tag, so the tests run the code under review, uncommitted included.
make_snapshot() {
  local dest="$1"
  mkdir -p "$dest"
  (cd "${repo:?}" && git ls-files -z -co --exclude-standard | while IFS= read -r -d '' file; do
    [[ -e "$file" ]] && printf '%s\0' "$file"
  done | tar --null -T - -cf -) | tar -xf - -C "$dest"
  git -C "$dest" init -q -b installer-test
  git -C "$dest" add -A
  # Test fixture only: this repository is deleted at the end of the test.
  git -C "$dest" -c user.name="DokoPilot test" -c user.email=test@dokopilot.invalid \
    commit -q -m "Snapshot of the working tree for the installer tests"
}

# start_relay GATEWAY: when this machine reaches the Internet through an HTTPS proxy on its
# loopback (out of the containers' reach), relay it from the test network's gateway. Sets
# proxy_url (empty without proxy) and ca_file (extra CA of a TLS-intercepting proxy, or empty).
forwarder_pid=""
start_relay() {
  local gateway="$1" proxy hostport proxy_host proxy_port
  proxy="${HTTPS_PROXY:-${https_proxy:-}}"
  proxy_url=""
  ca_file=""
  [[ -n "$proxy" ]] || return 0
  hostport="${proxy#*://}"
  hostport="${hostport%%/*}"
  hostport="${hostport##*@}"
  proxy_host="${hostport%:*}"
  proxy_port="${hostport##*:}"
  if [[ "$proxy_host" == 127.* || "$proxy_host" == localhost ]]; then
    python3 "${repo:?}/tests/system/tcp-forward.py" "$gateway" 3128 "$proxy_host" "$proxy_port" &
    forwarder_pid=$!
    proxy_url="http://$gateway:3128"
  else
    proxy_url="http://$hostport"
  fi
  local candidate="${DP_TEST_CA_FILE:-${SSL_CERT_FILE:-}}"
  if [[ -n "$candidate" && -r "$candidate" ]]; then
    ca_file="$candidate"
  fi
  echo "HTTPS goes through the proxy $proxy_url${ca_file:+ (extra CA $ca_file)}"
}

cleanup_relay() {
  if [[ -n "$forwarder_pid" ]]; then
    kill "$forwarder_pid" 2>/dev/null || true
  fi
}

# proxy_env: docker exec options that give the proxy and its CA to a command (test only; the
# installer itself only relies on the usual https_proxy variable).
proxy_env() {
  local ca=/usr/local/share/ca-certificates/dokopilot-test-ca.crt
  [[ -n "$proxy_url" ]] || return 0
  printf '%s\n' -e "HTTPS_PROXY=$proxy_url" -e "https_proxy=$proxy_url" -e "NO_PROXY=localhost,127.0.0.1" \
    -e "no_proxy=localhost,127.0.0.1"
  if [[ -n "$ca_file" ]]; then
    printf '%s\n' -e "SSL_CERT_FILE=$ca" -e "CURL_CA_BUNDLE=$ca" -e "PIP_CERT=$ca" -e "REQUESTS_CA_BUNDLE=$ca" -e "GIT_SSL_CAINFO=$ca"
  fi
}

# install_ca CONTAINER: copy the proxy's CA bundle into the container (test only).
install_ca() {
  local container="$1"
  [[ -n "$ca_file" ]] || return 0
  docker exec "$container" mkdir -p /usr/local/share/ca-certificates
  docker cp "$ca_file" "$container:/usr/local/share/ca-certificates/dokopilot-test-ca.crt" >/dev/null
  docker exec "$container" chmod 644 /usr/local/share/ca-certificates/dokopilot-test-ca.crt
}
