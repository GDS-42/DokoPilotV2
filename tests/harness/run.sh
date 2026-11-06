#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Harness tests: renders the workspace of the demo client with ansible/playbooks/harness.yml
# against a fake server and a fake Forgejo, then checks the clones, the credentials, the
# permissions, the models, the skills, the import, idempotence, the .bak of local changes
# and the behaviour of the guards on sample inputs. Nothing connects outside 127.0.0.1.
#
# The fake Forgejo (fake_forgejo.py) is made of bare repositories filled from context/ (the
# context role's templates, demo client) and served over HTTP by `git http-backend` with
# Basic authentication by token; the fake server is a folder of credential files read by
# play 1 without sudo (harness_server_become: false).
#
# HARNESS_TEST_KEEP=1 keeps the work folder (fake homes, workspaces, logs) for inspection.
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
servers=()
cleanup() {
  for pid in "${servers[@]}"; do kill "$pid" 2> /dev/null || true; done
  if [[ "${HARNESS_TEST_KEEP:-0}" == 1 ]]; then echo "work folder kept: $work"; else rm -rf "$work"; fi
}
trap cleanup EXIT
failures=0

ok() { echo "ok    $*"; }
fail() {
  echo "FAIL  $*"
  failures=$((failures + 1))
}

# Git used by the test itself, never influenced by the person's configuration.
tgit() { env -u GIT_DIR -u GIT_WORK_TREE GIT_CONFIG_NOSYSTEM=1 HOME="$work" git "$@"; }

# --------------------------------------------------------------- fake Forgejo and fake server
# start_forgejo <name> [--empty]: repositories in $work/<name>, state in $work/<name>-state.
start_forgejo() {
  local name="$1"
  shift
  "$venv_python" "$here/fake_forgejo.py" setup --root "$work/$name" --repo-dir "$repo" \
    --client "$repo/clients/demo/client.yml" --state "$work/$name-state" "$@"
  "$venv_python" "$here/fake_forgejo.py" serve --root "$work/$name" --users "$work/$name-state/users.json" \
    --port-file "$work/$name.port" --log "$work/$name.log" &
  servers+=("$!")
  for _ in $(seq 50); do [[ -s "$work/$name.port" ]] && return 0; sleep 0.1; done
  echo "fake Forgejo $name did not start" >&2
  exit 1
}
start_forgejo forgejo
start_forgejo forgejo-empty --empty
forgejo_url="http://127.0.0.1:$(cat "$work/forgejo.port")/"
model_key="test-model-K3y-0123456789"

# vars <file> <state> <forgejo url>: the server reads point at fake files; passwords and the
# model key are injected (never on the command line).
vars() {
  cat > "$1" <<EOF
harness_server_become: false
harness_server_credentials_dir: $2/assistants
harness_server_forgejo_credentials_dir: $2/forgejo
harness_tailscale_dns_name: dokos-demo.tail1234.ts.net.
harness_mail_password: test-mail-Pa55word
harness_calendar_password: test-cal-Pa55word
harness_model_api_key: $model_key
harness_import_date: "2026-11-06"
EOF
  if [[ -n "$3" ]]; then echo "dokopilot_forgejo_url: $3" >> "$1"; fi
}
vars "$work/fake-server.yml" "$work/forgejo-state" "$forgejo_url"
vars "$work/fake-server-empty.yml" "$work/forgejo-empty-state" "http://127.0.0.1:$(cat "$work/forgejo-empty.port")/"
vars "$work/fake-server-no-url.yml" "$work/forgejo-state" ""
printf 'dokopilot:\n  hosts:\n    demo:\n      ansible_connection: local\n      ansible_python_interpreter: %s\n' \
  "$venv_python" > "$work/inventory.yml"

# A folder to import (an Obsidian vault): two notes, one marked executable, the vault's own
# settings, and a symbolic link to a file outside the vault; only the two notes are imported.
mkdir -p "$work/vault/notes" "$work/vault/.obsidian" "$work/outside"
printf '# Accueil\n' > "$work/vault/accueil.md"
printf '# Fournisseurs\n' > "$work/vault/notes/fournisseurs.md"
chmod 0755 "$work/vault/notes/fournisseurs.md"
printf '{}\n' > "$work/vault/.obsidian/app.json"
printf 'cle privee\n' > "$work/outside/id_test"
ln -s "$work/outside/id_test" "$work/vault/lien-ssh"
printf 'context:\n  import_path: %s\n' "$work/vault" > "$work/import.yml"
printf 'context:\n  import_path: ""\n' > "$work/no-import.yml"

# run_play <scenario> <client overrides> <vars file> <extra vars file> [ansible args...]
run_play() {
  local scenario="$1" overrides="$2" fake="$3" extra="$4"
  shift 4
  (cd "$repo/ansible" && HOME="$work/$scenario/home" ANSIBLE_CONFIG=ansible.cfg ANSIBLE_NOCOLOR=1 \
    "$playbook_bin" -i "$work/inventory.yml" \
    -e "@$repo/clients/demo/client.yml" -e "@$overrides" -e "@$extra" \
    -e "@$repo/tests/config/secrets.yml" -e "@$fake" \
    -e "harness_dest=$work/$scenario/workspace" "$@" \
    playbooks/harness.yml)
}

# Lines of the play recap that report a change or a failure.
recap_problems() {
  grep -E '^[A-Za-z0-9_.-]+ +: ok=' "$1" | grep -Ev ' changed=0 .* failed=0 ' || true
}

# scenario <name> <client overrides> <extra vars file> <person> -- <checker arguments...>
scenario() {
  local name="$1" overrides="$2" extra="$3" person="$4"
  shift 5
  local logs="$work/$name"
  mkdir -p "$work/$name/home"
  if ! run_play "$name" "$overrides" "$work/fake-server.yml" "$extra" -e "dokopilot_person=$person" > "$logs-run1.log" 2>&1; then
    fail "$name: first run"
    tail -n 40 "$logs-run1.log"
    return
  fi
  ok "$name: first run"
  if ! run_play "$name" "$overrides" "$work/fake-server.yml" "$extra" -e "dokopilot_person=$person" > "$logs-run2.log" 2>&1; then
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
    --config-dir "$work/$name/home/.config/dokopilot/demo" --state "$work/forgejo-state" --forgejo-url "$forgejo_url" \
    --secret fakeapikey0123456789 --secret fakeapisecret9876543210 --secret test-mail-Pa55word \
    --secret test-cal-Pa55word --secret "$model_key" "$@"; then
    ok "$name: clones, credentials, permissions, models and skills"
  else
    fail "$name: clones, credentials, permissions, models and skills"
  fi
  local person_token
  person_token="$("$venv_python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$work/forgejo-state/forgejo/$person.json")"
  if grep -qF -e "$model_key" -e fakeapisecret9876543210 -e "$person_token" "$logs-run1.log" "$logs-run2.log"; then
    fail "$name: a secret appears in the playbook output"
  else
    ok "$name: no secret in the playbook output"
  fi
}

camille=(--person camille --person-name "Camille Martin" --person-email camille.martin@atelier-demo.test
  --assistant-name "Assistant de Camille" --assistant-email assistant.camille@atelier-demo.test --dirigeant)
lucas=(--person lucas --person-name "Lucas Bernard" --person-email lucas.bernard@atelier-demo.test
  --assistant-name "Assistant de Lucas" --assistant-email assistant.lucas@atelier-demo.test)

# in_background <name> <command...>: runs an independent check (own home and workspace) in
# the background; collect waits for all of them and prints their results in order.
background=()
in_background() {
  local name="$1"
  shift
  "$@" > "$work/$name.out" 2>&1 &
  background+=("$name:$!")
}
collect() {
  local entry
  for entry in "${background[@]}"; do
    wait "${entry#*:}" || true
    cat "$work/${entry%%:*}.out"
    failures=$((failures + $(grep -c '^FAIL' "$work/${entry%%:*}.out" || true)))
  done
  background=()
}

in_background lan scenario lan "$here/client-lan.yml" "$work/import.yml" camille -- "${camille[@]}" --dokos-url http://192.168.122.10 \
  --harness opencode --harness claude-code --mail --calendar --provider mistral --model-bureau mistral/mistral-medium-latest \
  --model-atelier mistral/devstral-medium-latest --model-small mistral/mistral-medium-latest --model-key --model-key-opencode \
  --import-date 2026-11-06
in_background membre scenario membre "$here/client-membre.yml" "$work/no-import.yml" lucas -- "${lucas[@]}" \
  --harness opencode --harness claude-code --model-key --claude-auth api_key
in_background tailscale scenario tailscale "$here/client-tailscale.yml" "$work/no-import.yml" camille -- "${camille[@]}" \
  --dokos-url https://dokos-demo.tail1234.ts.net --harness opencode --opencode-version 1 --provider anthropic \
  --model-bureau anthropic/claude-sonnet-4-5 --model-atelier anthropic/claude-opus-4-1 --model-small anthropic/claude-haiku-4-5 \
  --model-key --model-key-opencode
collect

ws="$work/lan/workspace"
cfg="$work/lan/home/.config/dokopilot/demo"
ws_m="$work/membre/workspace"

# ------------------------------------------------------------------- Forgejo access and updates
if grep -q '^lucas .*/direction.git' "$work/forgejo.log"; then fail "the member asked Forgejo for direction"; else ok "the member never asks for direction"; fi
if grep -q '^camille .*/direction.git' "$work/forgejo.log" && grep -q '^lucas .*/perso-lucas.git' "$work/forgejo.log"; then
  ok "Forgejo is reached with each person's own token"
else
  fail "Forgejo requests by camille and lucas"
fi

# push_server <repository> <file> <text>: a change made by someone else, on the server.
push_server() {
  local tmp="$work/push-$RANDOM"
  tgit clone -q "$work/forgejo/demo/$1.git" "$tmp"
  printf '%s\n' "$3" > "$tmp/$2"
  tgit -C "$tmp" add "$2"
  tgit -C "$tmp" -c user.name=Autre -c user.email=autre@atelier-demo.test commit -q -m "Change du serveur"
  tgit -C "$tmp" push -q origin main
}
push_server entreprise 20-Notes-permanentes/nouvelle-note.md "Note reçue du serveur"
if run_play membre "$here/client-membre.yml" "$work/fake-server.yml" "$work/no-import.yml" -e dokopilot_person=lucas > "$work/membre-run3.log" 2>&1 \
  && [[ -f "$ws_m/20-Notes-permanentes/nouvelle-note.md" ]] && [[ "$(tgit -C "$ws_m" rev-parse HEAD)" == "$(tgit -C "$ws_m" rev-parse '@{u}')" ]]; then
  ok "a clean clone is fast-forwarded to the server"
else
  fail "fast-forward of a clean clone"
  tail -n 20 "$work/membre-run3.log"
fi
# Camille has the import commit (not pushed): her clone has diverged, and stays as it is.
head_before="$(tgit -C "$ws" rev-parse HEAD)"
if run_play lan "$here/client-lan.yml" "$work/fake-server.yml" "$work/import.yml" -e dokopilot_person=camille > "$work/lan-run3.log" 2>&1 \
  && [[ "$(tgit -C "$ws" rev-parse HEAD)" == "$head_before" ]] && grep -q 'ont divergé' "$work/lan-run3.log" \
  && [[ "$(tgit -C "$ws" log --format=%s -- 00-Boite-de-reception | grep -c '^Importe')" == 1 ]]; then
  ok "a diverged clone is left as it is, the import is not repeated"
else
  fail "diverged clone or repeated import"
  tail -n 20 "$work/lan-run3.log"
fi

# ---------------------------------------------------------------- local changes are preserved
# A harness file changed on this computer is kept as .bak; AGENTS.md belongs to the company
# and is never touched by the harness; a dirty clone is not updated.
printf '\nNote locale ajoutée par le test.\n' >> "$ws/.opencode/agents/bureau.md"
printf '\nRègle ajoutée sur ce poste.\n' >> "$ws/AGENTS.md"
if run_play lan "$here/client-lan.yml" "$work/fake-server.yml" "$work/import.yml" -e dokopilot_person=camille > "$work/lan-run4.log" 2>&1; then
  backup="$(find "$ws/.opencode/agents" -maxdepth 1 -name 'bureau.md.*.bak' -print -quit)"
  if [[ -n "$backup" ]] && grep -q 'Note locale ajoutée' "$backup" && ! grep -q 'Note locale ajoutée' "$ws/.opencode/agents/bureau.md"; then
    ok "local change of a harness file kept in $(basename "$backup")"
  else
    fail "local change of a harness file not kept in a .bak"
  fi
  if [[ -n "$(find "$ws" -name '*.bak' ! -name 'bureau.md.*' -not -path '*/.git/*' -print -quit)" ]]; then fail "unchanged files were backed up"; fi
  if grep -q 'Règle ajoutée sur ce poste' "$ws/AGENTS.md" && grep -q 'modifications locales non enregistrées' "$work/lan-run4.log"; then
    ok "AGENTS.md is the repository's: kept, and the dirty clone is not updated"
  else
    fail "AGENTS.md overwritten or dirty clone updated"
  fi
  if [[ "$(tgit -C "$ws" status --porcelain)" == " M AGENTS.md" ]]; then ok "the .bak files are ignored by Git"; else fail "git status after .bak: $(tgit -C "$ws" status --porcelain)"; fi
else
  fail "fourth run (local changes)"
  tail -n 40 "$work/lan-run4.log"
fi
tgit -C "$ws" checkout -q -- AGENTS.md

# The company's .gitignore (context role) ignores the harness files on its own as well.
tgit clone -q "$work/forgejo/demo/entreprise.git" "$work/plain-clone"
missing=""
for path in opencode.json .mcp.json .claude/settings.json .opencode/agents/bureau.md .agents/skills/dokopilot-devis/SKILL.md direction/x perso/x; do
  tgit -C "$work/plain-clone" check-ignore -q --no-index "$path" || missing+=" $path"
done
if [[ -z "$missing" ]]; then ok "context/entreprise/.gitignore ignores the harness files"; else fail "not ignored by the company's .gitignore:$missing"; fi

# ------------------------------------------------------------------------- failure messages
expect_failure() {
  local label="$1" pattern="$2" log="$work/$3.log"
  shift 3
  if "$@" > "$log" 2>&1; then
    fail "$label: the run succeeded"
  elif grep -q "$pattern" "$log"; then
    ok "$label"
  else
    fail "$label: message not found"
    tail -n 20 "$log"
  fi
}
mkdir -p "$work/vide/home" "$work/ancien/home" "$work/ancien/workspace" "$work/sans-url/home"
in_background vide expect_failure "empty entreprise repository refused in French" "encore vide sur Forgejo" vide \
  run_play vide "$here/client-lan.yml" "$work/fake-server-empty.yml" "$work/no-import.yml" -e dokopilot_person=camille
printf 'ancien\n' > "$work/ancien/workspace/AGENTS.md"
in_background ancien expect_failure "workspace of version 0.1 refused in French" "n'est pas une copie Git" ancien \
  run_play ancien "$here/client-lan.yml" "$work/fake-server.yml" "$work/no-import.yml" -e dokopilot_person=camille
# Without an injected address, play 1 computes it with the forgejo role (lan: server.address:3000).
printf 'server:\n  address: 127.0.0.1\n' > "$work/local-address.yml"
in_background sans-url expect_failure "address of Forgejo from the forgejo role" "http://127.0.0.1:3000/demo/entreprise.git" sans-url \
  run_play sans-url "$here/client-lan.yml" "$work/fake-server-no-url.yml" "$work/local-address.yml" -e dokopilot_person=camille
collect
# An agent that planted a program in the configuration of a clone stops the next run.
tgit -C "$ws_m/perso" config --local core.fsmonitor "touch $work/pwned"
expect_failure "planted Git configuration refused" "réglages Git inattendus : core.fsmonitor" membre-planted \
  run_play membre "$here/client-membre.yml" "$work/fake-server.yml" "$work/no-import.yml" -e dokopilot_person=lucas
if [[ -e "$work/pwned" ]]; then fail "the planted fsmonitor ran"; fi
tgit -C "$ws_m/perso" config --local --unset core.fsmonitor

# ------------------------------------------------------------------- Claude Code hooks
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
  hook_case "Bureau edit in the inbox follows the rules" silent "$(pre s-fresh bureau Edit "{\"file_path\": \"$ws/00-Boite-de-reception/note.md\"}")"
  hook_case "Bureau write in perso/ follows the rules" silent "$(pre s-fresh bureau Write "{\"file_path\": \"$ws/perso/memoire-vive.md\"}")"
  hook_case "Bureau edit of a permanent note asks" ask "$(pre s-fresh bureau Edit "{\"file_path\": \"$ws/20-Notes-permanentes/x.md\"}")"
  hook_case "Bureau edit of a relative path asks" ask "$(pre s-fresh bureau Edit '{"file_path": "00-Boite-de-reception/x.md"}')"
  mkdir -p "$work/escape"
  ln -s "$work/escape" "$ws/00-Boite-de-reception/lien-test"
  hook_case "Bureau edit through a link out of the inbox asks" ask "$(pre s-fresh bureau Edit "{\"file_path\": \"$ws/00-Boite-de-reception/lien-test/x.md\"}")"
  rm "$ws/00-Boite-de-reception/lien-test"
  hook_case "mail read is allowed and taints" silent "$(pre s-mail bureau mcp__mail__get_emails_content '{"account_name": "principal", "email_ids": ["1"]}')"
  if [[ -f "$state/s-mail" ]]; then ok "hook: taint marker written before the mail read"; else fail "hook: no taint marker"; fi
  hook_case "inbox edit after a mail read asks" ask "$(pre s-mail bureau Edit "{\"file_path\": \"$ws/00-Boite-de-reception/note.md\"}")"
  hook_case "Dokos write after a mail read asks" ask "$(pre s-mail bureau mcp__dokos__doc_create '{"doc": {}}')"
  hook_case "Bash in the Atelier after a mail read asks" ask "$(pre s-mail atelier Bash '{"command": "ls"}')"
  hook_case "WebFetch after a mail read asks" ask "$(pre s-mail atelier WebFetch '{"url": "https://example.test"}')"
  hook_case "read tool after a mail read stays silent" silent "$(pre s-mail bureau Read '{"file_path": "/tmp/x"}')"
  hook_case "PostToolUse on a mail tool" silent "$(post s-post mcp__mail__list_emails_metadata)"
  hook_case "Edit in the Atelier after PostToolUse taint asks" ask "$(pre s-post atelier Edit '{"file_path": "/tmp/x"}')"
  hook_case "Atelier launched as a subagent is denied" deny "$(pre s-fresh bureau Agent '{"subagent_type": "atelier", "prompt": "x"}')"
  hook_case "invalid session id fails closed" ask "$(pre '../escape' atelier Bash '{"command": "ls"}')"
  hook_case "mail read without usable session id is blocked" block "$(pre '' bureau mcp__mail__get_emails_content '{}')"
  hook_case "malformed input is blocked" block 'not json'
  hook_case "input without tool name is blocked" block '{"session_id": "s-fresh", "hook_event_name": "PreToolUse"}'
  hook_case "missing policy is blocked" block "$(pre s-fresh atelier Read '{}')" "$work/missing-policy.json"
  "$venv_python" -c 'import json,sys; p=json.load(open(sys.argv[1])); p.pop("free_write_dirs"); json.dump(p, open(sys.argv[2], "w"))' \
    "$policy" "$work/old-policy.json"
  hook_case "policy without free-write folders is blocked" block "$(pre s-fresh atelier Read '{}')" "$work/old-policy.json"

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

# apiKeyHelper of the member (models.claude_code_auth: api_key), run as Claude Code would.
helper="$("$venv_python" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("apiKeyHelper", ""))' "$ws_m/.claude/settings.json")"
if [[ -n "$helper" && "$(sh -c "$helper")" == "$model_key" ]]; then ok "apiKeyHelper prints the model key"; else fail "apiKeyHelper output"; fi
if [[ "$(sh -c "$helper" | wc -l)" == 1 ]] && ! "$hook_python" "$ws_m/.claude/hooks/model_key_helper.py" "$work/missing-key" 2> /dev/null; then
  ok "apiKeyHelper prints one line, and fails on a missing key"
else
  fail "apiKeyHelper output format or failure"
fi

# The agent's commits carry the assistant's name: Claude Code applies `env` to its session.
eval "$("$venv_python" -c 'import json,shlex,sys; print(" ".join("export %s=%s;" % (k, shlex.quote(v)) for k, v in json.load(open(sys.argv[1]))["env"].items()))' "$ws_m/.claude/settings.json")"
printf 'Brouillon de l agent\n' > "$ws_m/perso/00-Boite-de-reception/brouillon-agent.md"
tgit -C "$ws_m/perso" add 00-Boite-de-reception/brouillon-agent.md
tgit -C "$ws_m/perso" commit -q -m "Ajoute un brouillon"
unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
if [[ "$(tgit -C "$ws_m/perso" log -1 --format='%an <%ae>|%cn')" == "Assistant de Lucas <assistant.lucas@atelier-demo.test>|Assistant de Lucas" ]]; then
  ok "a commit made with the Claude Code environment is signed « Assistant de Lucas »"
else
  fail "author of the agent's commit: $(tgit -C "$ws_m/perso" log -1 --format='%an <%ae>|%cn')"
fi

# ------------------------------------------------------------------- opencode plugin and code
if command -v node > /dev/null; then
  if node --check "$repo/harness/opencode/plugins/dokopilot-guard.js"; then ok "opencode plugin syntax"; else fail "opencode plugin syntax"; fi
  if node "$here/plugin_check.mjs" "$ws/.opencode/plugins/dokopilot-guard.js" "Assistant de Camille" assistant.camille@atelier-demo.test; then
    ok "opencode plugin: identity and taint"
  else
    fail "opencode plugin: identity and taint"
  fi
else
  echo "skip  opencode plugin checks (node absent)"
fi
if "$venv_python" - "$repo"/harness/claude/hooks/*.py "$repo"/ansible/roles/harness/filter_plugins/*.py "$repo"/ansible/roles/harness/files/*.py <<'PY'; then
import ast, sys
for path in sys.argv[1:]:
    ast.parse(open(path, encoding="utf-8").read(), path)
PY
  ok "Python syntax of the hooks, filters and Git helper"
else
  fail "Python syntax of the hooks, filters and Git helper"
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
