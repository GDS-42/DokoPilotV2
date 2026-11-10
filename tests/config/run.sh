#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Fast tests, through the CLI as the integrator uses it: `bin/dokopilot config` merges
# client.yml (base.yml) and client.local.yml (each case), decrypts the age secrets and runs
# the config role. Valid settings pass; unsafe ones are refused with their French message.
#   # expect: <text>          a failing case must print this message
#   # expect-output: <regex>  a passing case must print this (effective configuration)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export DOKOPILOT_CLIENTS_DIR="$work/clients"
export DOKOPILOT_AGE_IDENTITY="$work/age-identity.txt"
export ANSIBLE_NOCOLOR=1 ANSIBLE_FORCE_COLOR=0
age-keygen -o "$DOKOPILOT_AGE_IDENTITY" 2> /dev/null
failures=0

for case_file in "$here"/cases/*.yml; do
  name="$(basename "$case_file" .yml)"
  client="$DOKOPILOT_CLIENTS_DIR/test"
  rm -rf "$client" && mkdir -p "$client"
  cp "$here/base.yml" "$client/client.yml"
  cp "$case_file" "$client/client.local.yml"
  age-keygen -y "$DOKOPILOT_AGE_IDENTITY" > "$client/recipients.txt"
  if [[ "$name" == "fail-missing-secret" ]]; then
    grep -v 'restic_password' "$here/secrets.yml" > "$work/secrets.yml"
  else
    cp "$here/secrets.yml" "$work/secrets.yml"
  fi
  age --encrypt --armor --recipients-file "$client/recipients.txt" --output "$client/secrets.yml.age" "$work/secrets.yml"
  if "$repo/bin/dokopilot" config test > "$work/$name.log" 2>&1; then
    outcome=pass
  else
    outcome=fail
  fi
  expected="${name%%-*}"
  log="$(tr -s ' \n' ' ' < "$work/$name.log")"
  problems=""
  [[ "$outcome" == "$expected" ]] || problems="expected $expected, got $outcome"
  while IFS= read -r message; do
    [[ -z "$message" || "$log" == *"$message"* ]] || problems+=" missing message « $message »"
  done < <(sed -n 's/^# expect: //p' "$case_file")
  while IFS= read -r pattern; do
    [[ -z "$pattern" ]] || grep -Eq -- "$pattern" "$work/$name.log" || problems+=" missing output /$pattern/"
  done < <(sed -n 's/^# expect-output: //p' "$case_file")
  if [[ -z "$problems" ]]; then
    echo "ok    $name"
  else
    echo "FAIL  $name ($problems)"
    tail -n 20 "$work/$name.log"
    failures=$((failures + 1))
  fi
done

if (( failures > 0 )); then
  echo "$failures case(s) failed"
  exit 1
fi
echo "All configuration cases passed"
