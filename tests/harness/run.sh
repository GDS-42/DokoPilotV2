#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Harness tests: renders the workspace of the demo client with ansible/playbooks/harness.yml
# against a fake server (the server reads are injected, nothing connects anywhere), then
# checks the files, the secrets, the permissions, idempotence, the .bak of local changes and
# the behaviour of the Claude Code hooks on sample inputs.
#
# Optional: HARNESS_TEST_OPENCODE=1 also loads the rendered configuration with the installed
# `opencode` CLI (`opencode debug config`); no model session is started.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
venv_python="${PYTHON:-$repo/.venv/bin/python}"
playbook_bin="${ANSIBLE_PLAYBOOK:-$repo/.venv/bin/ansible-playbook}"
# The hooks run with the python3 of the workstation and the standard library only.
hook_python="${HOOK_PYTHON:-python3}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
failures=0

ok() { echo "ok    $*"; }
fail() {
  echo "FAIL  $*"
  failures=$((failures + 1))
}

# Fake server reads and passwords (test values): the checks make sure none of them reaches
# a workspace. They go through a vars file, never through the command line.
cat > "$work/fake-server.yml" <<'EOF'
harness_assistant_credentials:
  user: assistant.camille@atelier-demo.test
  api_key: fakeapikey0123456789
  api_secret: fakeapisecret9876543210
harness_tailscale_dns_name: dokos-demo.tail1234.ts.net.
harness_mail_password: test-mail-Pa55word
harness_calendar_password: test-cal-Pa55word
EOF
printf 'dokopilot:\n  hosts:\n    demo:\n      ansible_connection: local\n      ansible_python_interpreter: %s\n' \
  "$venv_python" > "$work/inventory.yml"

# run_play <scenario> <client overrides>: one run of the playbook, as bin/dokopilot would.
run_play() {
  local scenario="$1" overrides="$2"
  (cd "$repo/ansible" && HOME="$work/$scenario/home" ANSIBLE_CONFIG=ansible.cfg ANSIBLE_NOCOLOR=1 \
    "$playbook_bin" -i "$work/inventory.yml" \
    -e "@$repo/clients/demo/client.yml" -e "@$overrides" \
    -e "@$repo/tests/config/secrets.yml" -e "@$work/fake-server.yml" \
    -e "harness_dest=$work/$scenario/workspace" \
    playbooks/harness.yml)
}

# Lines of the play recap that report a change or a failure.
recap_problems() {
  grep -E '^[A-Za-z0-9_.-]+ +: ok=' "$1" | grep -Ev ' changed=0 .* failed=0 ' || true
}

# scenario <name> <client overrides> <checker arguments...>
scenario() {
  local name="$1" overrides="$2"
  shift 2
  local logs="$work/$name"
  mkdir -p "$work/$name/home"
  if ! run_play "$name" "$overrides" > "$logs-run1.log" 2>&1; then
    fail "$name: first run"
    tail -n 40 "$logs-run1.log"
    return
  fi
  ok "$name: first run"
  if ! run_play "$name" "$overrides" > "$logs-run2.log" 2>&1; then
    fail "$name: second run"
    tail -n 40 "$logs-run2.log"
    return
  fi
  if [[ -n "$(recap_problems "$logs-run2.log")" ]]; then
    fail "$name: the second run reports changes"
    grep -E '^(changed|failed):' "$logs-run2.log" | head -n 20 || true
  else
    ok "$name: second run without change"
  fi
  if "$venv_python" "$here/check_workspace.py" --workspace "$work/$name/workspace" \
    --config-dir "$work/$name/home/.config/dokopilot/demo" --fake-vars "$work/fake-server.yml" "$@"; then
    ok "$name: workspace, secrets and permissions"
  else
    fail "$name: workspace, secrets and permissions"
  fi
}

scenario lan "$here/client-lan.yml" --dokos-url http://192.168.122.10 \
  --harness opencode --harness claude-code --mail --calendar
scenario tailscale "$here/client-tailscale.yml" --dokos-url https://dokos-demo.tail1234.ts.net \
  --harness opencode --opencode-version 1

ws="$work/lan/workspace"
cfg="$work/lan/home/.config/dokopilot/demo"

# A file changed on this computer is kept as .bak before the new version replaces it.
if [[ -f "$ws/AGENTS.md" ]]; then
  printf '\nNote locale ajoutée par le test.\n' >> "$ws/AGENTS.md"
  if run_play lan "$here/client-lan.yml" > "$work/lan-run3.log" 2>&1; then
    backup="$(find "$ws" -maxdepth 1 -name 'AGENTS.md.*.bak' -print -quit)"
    if [[ -n "$backup" ]] && grep -q 'Note locale ajoutée' "$backup" && ! grep -q 'Note locale ajoutée' "$ws/AGENTS.md"; then
      ok "local change kept in $(basename "$backup")"
    else
      fail "local change of AGENTS.md not kept in a .bak"
    fi
    if [[ -n "$(find "$ws" -name '*.bak' ! -name 'AGENTS.md.*' -print -quit)" ]]; then
      fail "unchanged files were backed up"
    fi
  else
    fail "third run (local change)"
    tail -n 40 "$work/lan-run3.log"
  fi
fi

# Claude Code hooks on sample inputs: silent (no decision), ask, deny or block (exit 2).
hook="$ws/.claude/hooks/mail_taint_guard.py"
policy="$ws/.claude/hooks/guard-policy.json"
state="$cfg/taint"
hook_case() {
  local label="$1" expected="$2" input="$3" out status=0 result=1
  out="$(printf '%s' "$input" | "$hook_python" "$hook" --state-dir "$state" --policy "${4:-$policy}" 2> "$work/hook.err")" || status=$?
  case "$expected" in
    silent) if [[ $status -eq 0 && -z "$out" ]]; then result=0; fi ;;
    ask | deny) if [[ $status -eq 0 && "$out" == *"\"permissionDecision\": \"$expected\""* ]]; then result=0; fi ;;
    block) if [[ $status -eq 2 && -z "$out" ]]; then result=0; fi ;;
  esac
  if [[ $result -eq 0 ]]; then
    ok "hook: $label"
  else
    fail "hook: $label (expected $expected, exit $status, output: $out $(cat "$work/hook.err"))"
  fi
}
pre() { printf '{"session_id": "%s", "hook_event_name": "PreToolUse", "agent_type": "%s", "tool_name": "%s", "tool_input": %s}' "$@"; }
post() { printf '{"session_id": "%s", "hook_event_name": "PostToolUse", "tool_name": "%s", "tool_input": {}, "tool_response": {}}' "$@"; }

if [[ -f "$hook" ]]; then
  hook_case "read tool in a fresh session" silent "$(pre s-fresh bureau Read '{"file_path": "/tmp/x"}')"
  hook_case "Dokos read in a fresh session" silent "$(pre s-fresh bureau mcp__dokos__doc_list '{}')"
  hook_case "Bash in the Bureau asks" ask "$(pre s-fresh bureau Bash '{"command": "ls"}')"
  hook_case "Bash in the Atelier, fresh session" silent "$(pre s-fresh atelier Bash '{"command": "ls"}')"
  hook_case "Bash without agent type is treated as Bureau" ask '{"session_id": "s-fresh", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}'
  hook_case "mail read is allowed and taints" silent "$(pre s-mail bureau mcp__mail__get_emails_content '{"account_name": "principal", "email_ids": ["1"]}')"
  if [[ -f "$state/s-mail" ]]; then ok "hook: taint marker written before the mail read"; else fail "hook: no taint marker"; fi
  hook_case "Dokos write after a mail read asks" ask "$(pre s-mail bureau mcp__dokos__doc_create '{"doc": {}}')"
  hook_case "Bash in the Atelier after a mail read asks" ask "$(pre s-mail atelier Bash '{"command": "ls"}')"
  hook_case "WebFetch after a mail read asks" ask "$(pre s-mail atelier WebFetch '{"url": "https://example.test"}')"
  hook_case "read tool after a mail read stays silent" silent "$(pre s-mail bureau Read '{"file_path": "/tmp/x"}')"
  hook_case "mail draft after a mail read asks" ask "$(pre s-mail bureau mcp__mail__save_draft '{}')"
  hook_case "PostToolUse on a mail tool" silent "$(post s-post mcp__mail__list_emails_metadata)"
  hook_case "Edit in the Atelier after PostToolUse taint asks" ask "$(pre s-post atelier Edit '{"file_path": "/tmp/x"}')"
  hook_case "Atelier launched as a subagent is denied" deny "$(pre s-fresh bureau Agent '{"subagent_type": "atelier", "prompt": "x"}')"
  hook_case "invalid session id fails closed" ask "$(pre '../escape' atelier Bash '{"command": "ls"}')"
  hook_case "mail read without usable session id is blocked" block "$(pre '' bureau mcp__mail__get_emails_content '{}')"
  hook_case "malformed input is blocked" block 'not json'
  hook_case "input without tool name is blocked" block '{"session_id": "s-fresh", "hook_event_name": "PreToolUse"}'
  hook_case "missing policy is blocked" block "$(pre s-fresh atelier Read '{}')" "$work/missing-policy.json"

  token="$("$hook_python" "$ws/.claude/hooks/dokos_headers.py" "$cfg/dokos-token")"
  if [[ "$token" == '{"Authorization": "token fakeapikey0123456789:fakeapisecret9876543210"}' ]]; then
    ok "headersHelper prints the Authorization header"
  else
    fail "headersHelper output"
  fi
  launched="$("$hook_python" "$ws/.claude/hooks/caldav_launcher.py" "$cfg/caldav-password" "$hook_python" -c \
    'import os, sys; sys.stdout.write("ok" if os.environ.get("CALDAV_PASSWORD") == "test-cal-Pa55word" else "ko")')"
  if [[ "$launched" == ok ]]; then ok "CalDAV launcher passes the password to the server only"; else fail "CalDAV launcher"; fi
else
  fail "Claude Code hook not rendered"
fi

# Syntax of the code shipped to the workstation.
if "$venv_python" - "$repo"/harness/claude/hooks/*.py "$repo"/ansible/roles/harness/filter_plugins/*.py <<'PY'; then
import ast, sys
for path in sys.argv[1:]:
    ast.parse(open(path, encoding="utf-8").read(), path)
PY
  ok "Python syntax of the hooks and filters"
else
  fail "Python syntax of the hooks and filters"
fi
if command -v node > /dev/null; then
  if node --check "$repo/harness/opencode/plugins/dokopilot-guard.js"; then ok "opencode plugin syntax"; else fail "opencode plugin syntax"; fi
else
  echo "skip  opencode plugin syntax (node absent)"
fi

if [[ "${HARNESS_TEST_OPENCODE:-0}" == 1 ]]; then
  if (cd "$ws" && HOME="$work/lan/home" opencode debug config > "$work/opencode-config.log" 2>&1); then
    ok "opencode loads the rendered configuration"
  else
    fail "opencode debug config"
    tail -n 20 "$work/opencode-config.log"
  fi
fi

if ((failures > 0)); then
  echo "$failures harness check(s) failed"
  exit 1
fi
echo "All harness checks passed"
