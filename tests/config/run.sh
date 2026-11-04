#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Fast tests: the config role accepts valid client settings and refuses unsafe ones.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
playbook_bin="${ANSIBLE_PLAYBOOK:-$repo/.venv/bin/ansible-playbook}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

printf 'dokopilot:\n  hosts:\n    test:\n      ansible_connection: local\n' > "$work/inventory.yml"
failures=0

for case_file in "$here"/cases/*.yml; do
  name="$(basename "$case_file" .yml)"
  secrets_file="$here/secrets.yml"
  if [[ "$name" == "fail-missing-secret" ]]; then
    grep -v 'restic_password' "$here/secrets.yml" > "$work/secrets.yml"
    secrets_file="$work/secrets.yml"
  fi
  # Merge base and case the same way the CLI does (deep merge, lists replaced).
  python3 - "$here/base.yml" "$case_file" "$work/merged.yml" <<'PY'
import sys, yaml
def merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
base = yaml.safe_load(open(sys.argv[1])) or {}
case = yaml.safe_load(open(sys.argv[2])) or {}
yaml.safe_dump(merge(base, case), open(sys.argv[3], "w"), allow_unicode=True)
PY
  if (cd "$repo/ansible" && ANSIBLE_CONFIG=ansible.cfg "$playbook_bin" -i "$work/inventory.yml" \
        -e "@$work/merged.yml" -e "@$secrets_file" playbooks/config.yml > "$work/$name.log" 2>&1); then
    outcome=pass
  else
    outcome=fail
  fi
  expected="${name%%-*}"
  message="$(sed -n 's/^# expect: //p' "$case_file")"
  if [[ "$outcome" == "$expected" && ( -z "$message" || "$(tr -s ' \n' ' ' < "$work/$name.log")" == *"$message"* ) ]]; then
    echo "ok    $name"
  else
    echo "FAIL  $name (expected $expected, got $outcome)"
    tail -n 20 "$work/$name.log"
    failures=$((failures + 1))
  fi
done

if (( failures > 0 )); then
  echo "$failures case(s) failed"
  exit 1
fi
echo "All configuration cases passed"
