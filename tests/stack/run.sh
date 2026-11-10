#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Dokos stack test, against THIS machine (Docker required, about 8 GB of RAM, 15 GB of
# disk for the images): runs the roles of ansible/playbooks/dokos.yml with
# ansible_connection=local, in a test directory and a test Compose project, with the demo
# client settings and secrets generated for the run. Then proves with real HTTP calls:
# site, company and chart of accounts, demo data, received e-invoices turned into
# Supplier Invoices, MCP with the assistant's token, drafts allowed but submit and delete
# refused, no access to the point of sale; check.yml passes; a second run changes
# nothing; backup then restore round-trips. Everything is removed at the end except the
# Docker images (DOKOPILOT_TEST_KEEP=1 keeps the stack for debugging).
#
# Environment: DOKOPILOT_TEST_PREFIX (default /opt/dokopilot-test), DOKOPILOT_TEST_PORT
# (default: a free port), DOKOPILOT_TEST_BUILD_CA (extra CA for an image build behind a
# TLS-intercepting proxy; HTTPS_PROXY and NO_PROXY are passed to the build when set).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
venv_python="$repo/.venv/bin/python"
playbook_bin="${ANSIBLE_PLAYBOOK:-$repo/.venv/bin/ansible-playbook}"
prefix="${DOKOPILOT_TEST_PREFIX:-/opt/dokopilot-test}"
project=dokopilot-test
company="Atelier Démo"
started=$(date +%s)

if [[ $EUID -ne 0 ]]; then
  echo "Ce test installe une pile Docker et des paquets : lancez-le en root." >&2
  exit 2
fi
for tool in docker curl jq ssh-keygen; do
  command -v "$tool" >/dev/null || { echo "Outil manquant : $tool" >&2; exit 2; }
done
if [[ -e "$prefix" ]]; then
  echo "$prefix existe déjà : supprimez-le (ou choisissez DOKOPILOT_TEST_PREFIX)." >&2
  exit 2
fi

work="$(mktemp -d)"
chmod 700 "$work"
port="${DOKOPILOT_TEST_PORT:-$("$venv_python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')}"
base="http://127.0.0.1:$port"
failures=0

cleanup() {
  local code=$?
  if [[ "${DOKOPILOT_TEST_KEEP:-0}" == 1 ]]; then
    echo "Pile conservée : $prefix (projet $project, port $port), fichiers de test : $work"
    return
  fi
  docker compose -p "$project" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$prefix" "$work"
  exit "$code"
}
trap cleanup EXIT

ok() { echo "ok    $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
check() { # check <description> <command...>
  local description="$1"
  shift
  if "$@"; then ok "$description"; else fail "$description"; fi
}

# --------------------------------------------------------------------------- inputs

# The Python of the distribution (with the apt bindings) runs the Ansible modules.
interpreter=/usr/bin/python3
for candidate in /usr/bin/python3 /usr/bin/python3.*; do
  if [[ -x "$candidate" && "$candidate" != *-config ]] && "$candidate" -c 'import apt_pkg' 2>/dev/null; then
    interpreter="$candidate"
    break
  fi
done

ssh-keygen -q -t ed25519 -N '' -C test@dokopilot -f "$work/maintenance_key"
printf 'dokopilot:\n  hosts:\n    demo:\n      ansible_connection: local\n      ansible_python_interpreter: %s\n' "$interpreter" > "$work/inventory.yml"

build_network=""
build_ca=""
if [[ -n "${HTTPS_PROXY:-}" ]]; then
  build_network=host
  build_ca="${DOKOPILOT_TEST_BUILD_CA:-${SSL_CERT_FILE:-}}"
fi
systemd=false
[[ -d /run/systemd/system ]] && systemd=true

umask 077
"$venv_python" - "$repo" "$work" "$prefix" "$port" "$build_network" "$build_ca" "$systemd" <<'PY'
import json, os, secrets, sys
import yaml

repo, work, prefix, port, build_network, build_ca, systemd = sys.argv[1:8]
client = yaml.safe_load(open(f"{repo}/clients/demo/client.yml", encoding="utf-8"))
client["server"]["address"] = "127.0.0.1"
client["maintenance"]["users"] = [
    {"name": "testeur", "full_name": "Testeur", "ssh_public_keys": [open(f"{work}/maintenance_key.pub").read().strip()]}
]
client["backup"] = {**client.get("backup", {}), "repository": f"{prefix}/restic"}
yaml.safe_dump(client, open(f"{work}/client.yml", "w", encoding="utf-8"), allow_unicode=True)

names = ["maintenance_sudo_password", "db_root_password", "dokos_admin_password", "entrepreneur_password", "restic_password"]
data = {"secrets": {name: secrets.token_urlsafe(24) for name in names}}
data["secrets"].update({"restic_env": {}, "tailscale_auth_key": "", "etransactions": {}, "ai_providers": {}})
yaml.safe_dump(data, open(f"{work}/secrets.yml", "w", encoding="utf-8"))

overrides = {
    "dokopilot_stack_dir": f"{prefix}/stack",
    "dokopilot_compose_project": "dokopilot-test",
    "dokopilot_etc_dir": f"{prefix}/etc",
    "dokopilot_bench_backup_dir": f"{prefix}/bench-backups",
    "dokopilot_dokos_http_port": int(port),
    "dokos_url": f"http://127.0.0.1:{port}",
    "backup_script": f"{prefix}/sbin/dokopilot-backup",
    "restore_script": f"{prefix}/sbin/dokopilot-restore",
    "backup_systemd_dir": f"{prefix}/systemd",
    "backup_enable_timer": systemd == "true",
}
if build_network:
    overrides["dokos_build_network"] = build_network
    proxy = {k: os.environ[k] for k in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy") if os.environ.get(k)}
    overrides["dokos_build_args"] = proxy
if build_ca:
    overrides["dokos_build_ca_file"] = build_ca
yaml.safe_dump(overrides, open(f"{work}/vars.yml", "w", encoding="utf-8"))
# For the HTTP checks of this script: the Administrator's login, as a form body file.
with open(f"{work}/login.json", "w", encoding="utf-8") as handle:
    json.dump({"usr": "Administrator", "pwd": data["secrets"]["dokos_admin_password"]}, handle)
PY
umask 022
mkdir -p "$prefix/sbin" "$prefix/systemd"

playbook() { # playbook <log name> <playbook> [extra args...]
  local log="$work/$1.log" book="$2"
  shift 2
  (cd "$repo/ansible" && ANSIBLE_CONFIG=ansible.cfg "$playbook_bin" -i "$work/inventory.yml" \
      -e "@$work/client.yml" -e "@$work/secrets.yml" -e "@$work/vars.yml" "$@" "playbooks/$book") > "$log" 2>&1
}
recap() { grep -A1 '^PLAY RECAP' "$work/$1.log" | tail -n 1; }
show_failure() { echo "--- fin du journal $1 ---"; tail -n 40 "$work/$1.log"; }

# --------------------------------------------------------------------------- HTTP helpers

admin_login() {
  curl -fsS -c "$work/cookies" -H 'Content-Type: application/json' --data "@$work/login.json" "$base/api/method/login" >/dev/null
}
api() { # api <path> : GET as Administrator
  curl -fsS -b "$work/cookies" -H 'Accept: application/json' "$base$1"
}
count() { # count <doctype> [filters json]
  local filters="${2:-[]}"
  api "/api/method/frappe.client.get_count?doctype=$(jq -rn --arg v "$1" '$v|@uri')&filters=$(jq -rn --arg v "$filters" '$v|@uri')" | jq -r '.message'
}
token_header() { # writes the curl header file of the assistant's token
  jq -r '"Authorization: token \(.api_key):\(.api_secret)"' "$prefix/etc/assistants/assistant.camille@atelier-demo.test.json" > "$work/token.header"
}
mcp() { # mcp <json-rpc body> : POST to the MCP endpoint with the assistant's token
  curl -sS -H "@$work/token.header" -H 'Content-Type: application/json' -H 'MCP-Protocol-Version: 2025-11-25' \
    --data "$1" "$base/api/method/dokos_ai.mcp.endpoint"
}
tool_call() { # tool_call <tool> <arguments json>
  mcp "$(jq -cn --arg name "$1" --argjson args "$2" '{jsonrpc: "2.0", id: 1, method: "tools/call", params: {name: $name, arguments: $args}}')"
}
equals() { [[ "$1" == "$2" ]] || { echo "      attendu « $2 », obtenu « $1 »"; return 1; }; }
at_least() { (( $1 >= $2 )) || { echo "      attendu au moins $2, obtenu $1"; return 1; }; }

# --------------------------------------------------------------------------- 1. install

echo "Installation de la pile de test (port $port, $prefix) ; la première construction de l'image prend plusieurs minutes…"
t0=$(date +%s)
if playbook install dokos.yml; then ok "dokos.yml (premier passage, $(( $(date +%s) - t0 )) s) : $(recap install)"; else fail "dokos.yml (premier passage)"; show_failure install; exit 1; fi
install_seconds=$(( $(date +%s) - t0 ))

admin_login
check "le site répond (ping)" equals "$(curl -fsS "$base/api/method/ping" | jq -r .message)" pong
company_json="$(api "/api/resource/Company/$(jq -rn --arg v "$company" '$v|@uri')")"
check "société « $company » en France, en euros" equals "$(jq -r '.data | "\(.country) \(.default_currency)"' <<<"$company_json")" "France EUR"
check "plan comptable général" equals "$(jq -r '.data.chart_of_accounts' <<<"$company_json")" "Plan Comptable Général"
check "comptes du plan comptable" at_least "$(count Account "[[\"company\",\"=\",\"$company\"]]")" 100
check "modèles de TVA à 20 %" at_least "$(count 'Sales Taxes and Charges Template' '[["title","like","TVA 20%"]]')" 1
check "6 clients" equals "$(count Customer)" 6
check "6 fournisseurs" equals "$(count Supplier)" 6
check "12 articles" equals "$(count Item)" 12
check "4 commandes d'achat validées" equals "$(count 'Purchase Order' '[["docstatus","=",1]]')" 4
check "3 réceptions validées" equals "$(count 'Purchase Receipt' '[["docstatus","=",1]]')" 3
check "8 factures de vente validées" equals "$(count 'Sales Invoice' '[["docstatus","=",1]]')" 8
check "factures de vente échues" at_least "$(count 'Sales Invoice' '[["status","in",["Overdue","Partly Paid and Overdue"]]]')" 2
check "factures de vente payées" at_least "$(count 'Sales Invoice' '[["status","=","Paid"]]')" 2
check "paiements validés" equals "$(count 'Payment Entry' '[["docstatus","=",1]]')" 4
check "7 opérations bancaires" equals "$(count 'Bank Transaction' '[["docstatus","=",1]]')" 7
check "5 factures électroniques reçues" equals "$(count eInvoice '[["einvoice_type","=","Incoming"]]')" 5
check "5 factures fournisseurs à traiter, créées par eTransactions" equals "$(count 'Supplier Invoice' '[["einvoice","is","set"]]')" 5
check "factures reçues conformes EN 16931 (validation d'eTransactions)" equals "$(count eInvoice '[["einvoice_type","=","Incoming"],["e_invoice_is_correct","=",1]]')" 5
check "aucune facture d'achat validée automatiquement" equals "$(count 'Purchase Invoice' '[["docstatus","=",1]]')" 1

# --------------------------------------------------------------------------- 2. assistant through MCP

credentials="$prefix/etc/assistants/assistant.camille@atelier-demo.test.json"
check "jeton de l'assistant écrit en 0600 (root)" equals "$(stat -c '%a %U' "$credentials")" "600 root"
check "dossier des jetons en 0700" equals "$(stat -c '%a' "$prefix/etc/assistants")" "700"
check "fichier de jeton au format {user, api_key, api_secret}" equals "$(jq -r 'keys | join(",")' "$credentials")" "api_key,api_secret,user"
token_header
init="$(mcp '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}')"
check "MCP initialize avec le jeton de l'assistant" equals "$(jq -r '.result.serverInfo.name' <<<"$init")" dokos
tools="$(mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | jq -r '[.result.tools[].name] | join(" ")')"
check "MCP tools/list ($tools)" equals "$([[ "$tools" == *doc_list* && "$tools" == *doc_create* ]] && echo yes)" yes

draft="$(tool_call doc_create '{"doc":{"doctype":"Purchase Invoice","supplier":"Scierie Démo du Jura","company":"Atelier Démo","bill_no":"TEST-MCP-1","items":[{"item_code":"PLANCHE-CHENE","qty":1,"rate":45}]}}')"
pi_name="$(api "/api/resource/Purchase%20Invoice?filters=$(jq -rn '[["bill_no","=","TEST-MCP-1"]]|tojson|@uri')" | jq -r '.data[0].name // empty')"
check "l'assistant crée une facture d'achat en brouillon par MCP ($pi_name)" equals "$(jq -r '.result.isError' <<<"$draft")/${pi_name:+found}" "false/found"
check "le brouillon est bien au statut brouillon" equals "$(api "/api/resource/Purchase%20Invoice/$pi_name" | jq -r '.data.docstatus')" 0
submit="$(tool_call doc_submit "{\"doctype\":\"Purchase Invoice\",\"name\":\"$pi_name\"}")"
check "doc_submit refusé à l'assistant ($(jq -r '.result.content[0].text' <<<"$submit" | head -c 90))" equals "$(jq -r '.result.isError' <<<"$submit")" true
check "la facture reste en brouillon" equals "$(api "/api/resource/Purchase%20Invoice/$pi_name" | jq -r '.data.docstatus')" 0
delete="$(tool_call doc_delete "{\"doctype\":\"Purchase Invoice\",\"name\":\"$pi_name\"}")"
check "doc_delete refusé à l'assistant" equals "$(jq -r '.result.isError' <<<"$delete")" true
check "la facture existe toujours" equals "$(api "/api/resource/Purchase%20Invoice/$pi_name" | jq -r '.data.name')" "$pi_name"
direct="$(tool_call doc_create '{"doc":{"doctype":"Purchase Invoice","supplier":"Scierie Démo du Jura","company":"Atelier Démo","bill_no":"TEST-MCP-2","docstatus":1,"items":[{"item_code":"PLANCHE-CHENE","qty":1,"rate":45}]}}')"
check "création directement validée (docstatus 1) refusée" equals "$(jq -r '.result.isError' <<<"$direct")" true
# Validation in disguise: doc_update setting docstatus to 1 (some Dokos AI skills suggest it).
update="$(tool_call doc_update "{\"doctype\":\"Purchase Invoice\",\"name\":\"$pi_name\",\"fields\":{\"docstatus\":1}}")"
check "doc_update avec docstatus 1 ne valide pas ($(jq -r '.result.content[0].text // ""' <<<"$update" | head -c 80))" \
  equals "$(api "/api/resource/Purchase%20Invoice/$pi_name" | jq -r '.data.docstatus')" 0
for doctype in "Sales Invoice" "Payment Entry" "Journal Entry"; do
  name="$(api "/api/resource/$(jq -rn --arg v "$doctype" '$v|@uri')?limit_page_length=1&filters=$(jq -rn '[["docstatus","=",1]]|tojson|@uri')" | jq -r '.data[0].name // empty')"
  [[ -z "$name" ]] && continue
  result="$(tool_call doc_cancel "{\"doctype\":\"$doctype\",\"name\":\"$name\"}")"
  check "doc_cancel refusé sur $doctype $name" equals "$(jq -r '.result.isError' <<<"$result")" true
done
for doctype in "POS Invoice" "POS Profile" "POS Opening Entry" "POS Closing Entry"; do
  result="$(tool_call doc_list "{\"doctype\":\"$doctype\"}")"
  check "lecture de « $doctype » (caisse) refusée" equals "$(jq -r '.result.isError' <<<"$result")" true
done
allowed="$(curl -fsS -H "@$work/token.header" "$base/api/method/frappe.client.has_permission?doctype=Purchase%20Invoice&docname=$pi_name&perm_type=submit" | jq -r '.message.has_permission')"
check "frappe.client.has_permission(submit) faux pour l'assistant" equals "$allowed" false

# --------------------------------------------------------------------------- 3. bench, checks, idempotence

# bench commands with the dependencies of the derived image (click downgraded by
# litellm, docs/decisions.md D-12): new-site, setup and backup already ran above.
in_backend() { docker compose --project-directory "$prefix/stack" exec -T backend "$@"; }
is_date() { [[ "$1" =~ ^\"?[0-9]{4}-[0-9]{2}-[0-9]{2}\"?$ ]] || { echo "      obtenu « $1 »"; return 1; }; }
check "bench execute" is_date "$(in_backend bench --site erp.demo.local execute frappe.utils.nowdate 2>/dev/null | tail -n 1)"
migrate_quietly() { in_backend bench --site erp.demo.local migrate > "$work/migrate.log" 2>&1; }
check "bench migrate" migrate_quietly
check "bench list-apps (dokos_ai installé)" equals "$(in_backend bench --site erp.demo.local list-apps 2>/dev/null | grep -c '^dokos_ai')" 1


if playbook check check.yml --tags dokos; then ok "check.yml (partie Dokos ; la partie serveur est vérifiée par tests/system) : $(grep -o 'Résultat : [^"]*' "$work/check.log" | head -n 1)"; else fail "check.yml"; show_failure check; fi

t0=$(date +%s)
if playbook second dokos.yml; then
  if grep -A1 '^PLAY RECAP' "$work/second.log" | tail -n 1 | grep -q ' changed=0 '; then
    ok "second passage de dokos.yml sans changement ($(( $(date +%s) - t0 )) s) : $(recap second)"
  else
    fail "second passage de dokos.yml : des tâches ont changé : $(recap second)"
    grep -B1 '^changed:' "$work/second.log" | grep '^TASK' || true
  fi
else
  fail "second passage de dokos.yml"; show_failure second
fi

# --------------------------------------------------------------------------- 4. backup and restore

if playbook backup backup.yml; then ok "backup.yml"; else fail "backup.yml"; show_failure backup; fi
restic_cmd() {
  set -a
  # shellcheck disable=SC1091
  . "$prefix/etc/restic.env"
  set +a
  restic "$@"
}
check "instantané restic présent" at_least "$(restic_cmd snapshots --json --tag dokopilot | jq length)" 1
check "pas de .env de la pile dans la sauvegarde" equals "$(restic_cmd ls latest --tag dokopilot | grep -c '/stack/.env$' || true)" 0

marker="Client créé après la sauvegarde"
curl -fsS -b "$work/cookies" -H 'Content-Type: application/json' \
  --data "$(jq -cn --arg n "$marker" '{customer_name: $n, customer_type: "Company"}')" "$base/api/resource/Customer" >/dev/null
check "client témoin créé après la sauvegarde" equals "$(count Customer "[[\"customer_name\",\"=\",\"$marker\"]]")" 1

if playbook restore-refused restore.yml; then fail "restore.yml sans confirmation aurait dû être refusé"; else ok "restore.yml refusé sans -e restore_confirmed=true"; fi
check "le site n'a pas été touché par le refus" equals "$(count Customer "[[\"customer_name\",\"=\",\"$marker\"]]")" 1

if playbook restore restore.yml -e restore_confirmed=true; then ok "restore.yml -e restore_confirmed=true"; else fail "restore.yml"; show_failure restore; fi
admin_login
check "le client témoin a disparu après la restauration" equals "$(count Customer "[[\"customer_name\",\"=\",\"$marker\"]]")" 0
check "les données de test sont revenues (6 clients)" equals "$(count Customer)" 6
check "le brouillon de l'assistant (antérieur à la sauvegarde) est revenu" equals "$(api "/api/resource/Purchase%20Invoice/$pi_name" | jq -r '.data.docstatus')" 0
check "le jeton de l'assistant fonctionne après la restauration" equals "$(mcp '{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}' | jq -r '.result.tools | length > 0')" true

# --------------------------------------------------------------------------- summary

image="$(docker compose -p "$project" images backend --format json 2>/dev/null | jq -r 'if type == "array" then .[0] else . end | "\(.Repository):\(.Tag) \(.Size)"' || true)"
memory="$(docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | awk -v p="$project-" 'index($1, p) == 1 { print }' \
  | "$venv_python" -c '
import re, sys
units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
total = 0
for line in sys.stdin:
    used = line.split()[1]
    value, unit = re.match(r"([0-9.]+)(\D+)", used).groups()
    total += float(value) * units[unit]
print(f"{total / 1024**2:.0f} MiB")')"
volumes="$(docker system df -v --format json 2>/dev/null | jq -r --arg p "${project}_" '[.Volumes[] | select(.Name | startswith($p)) | "\(.Name | sub($p; "")) \(.Size)"] | join(", ")' || true)"
echo "Pile : image $image ; mémoire des conteneurs $memory ; volumes $volumes"
echo "Durées : installation $install_seconds s, total $(( $(date +%s) - started )) s"

if (( failures > 0 )); then
  echo "$failures contrôle(s) en échec"
  exit 1
fi
echo "Tous les contrôles de la pile Dokos sont passés"
