#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Forgejo and preinstalled context test, against THIS machine (Docker required, about 300 MB
# of image): runs ansible/playbooks/context.yml (roles config, forgejo, context) with
# ansible_connection=local, in a test directory, a test Compose project and a free port,
# with the demo client (two people: camille, dirigeant, and lucas, membre, each with an
# assistant) and secrets generated for the run. First, without Docker, the unit checks of
# test_provision.py (reserved or colliding account names refused before any change). Then
# proves with real API and Git calls: accounts, organisation, teams, collaborators and
# repositories exist; each repository received the templates once (one commit by
# « DokoPilot ») and its default branch is protected; credential files are root 0600, a
# person's holds a token, an assistant's holds none and Forgejo holds none for it; mirrors
# are disabled; a second run changes nothing and adds no commit; check.yml (tag forgejo)
# passes; assistant-lucas (membre) cannot read direction nor push to perso-camille,
# assistant-camille clones direction; normal pushes work but a force push or the deletion
# of main is refused, even to the administrator of a perso repository; the copy and restore
# of the data volume documented in exploitation.md work; check.yml detects a removed or
# weakened protection and an assistant token (left by an earlier version), and the next
# run repairs them; a person removed from the configuration is disabled, loses access and
# their credential files, their repository is kept; a third run changes nothing.
# Everything is removed at the end except the Forgejo image (DOKOPILOT_TEST_KEEP=1 keeps it all).
#
# Environment: DOKOPILOT_TEST_PREFIX (default /opt/dokopilot-context-test),
# DOKOPILOT_TEST_PORT (default: a free port), ANSIBLE_PLAYBOOK.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
venv_python="$repo/.venv/bin/python"
playbook_bin="${ANSIBLE_PLAYBOOK:-$repo/.venv/bin/ansible-playbook}"
prefix="${DOKOPILOT_TEST_PREFIX:-/opt/dokopilot-context-test}"
project=dokopilot-forgejo-test
org=demo
started=$(date +%s)

if [[ $EUID -ne 0 ]]; then
  echo "Ce test installe un conteneur et écrit des fichiers root : lancez-le en root." >&2
  exit 2
fi
for tool in docker curl jq git ssh-keygen; do
  command -v "$tool" >/dev/null || { echo "Outil manquant : $tool" >&2; exit 2; }
done
[[ -x "$playbook_bin" ]] || { echo "ansible-playbook introuvable ($playbook_bin) : make setup" >&2; exit 2; }
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
    echo "Conservé : $prefix (projet $project, port $port), fichiers de test : $work"
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
equals() { [[ "$1" == "$2" ]] || { echo "      attendu « $2 », obtenu « $1 »"; return 1; }; }
contains() { grep -Fq -- "$2" <<<"$1" || { echo "      « $2 » absent"; return 1; }; }
refused() { if "$@"; then echo "      accepté : $*"; return 1; fi; }
quietly() { "$@" >/dev/null 2>&1; }
rejected_with() { # rejected_with <expected message> <command...> : the command fails and prints the message
  local expected="$1" output
  shift
  if output="$("$@" 2>&1)"; then echo "      accepté : $*"; return 1; fi
  contains "$output" "$expected"
}

# --------------------------------------------------------------------------- 0. unit checks (no Docker)

if (cd "$repo" && "$venv_python" -m unittest discover -s tests/context -p 'test_*.py') > "$work/unit.log" 2>&1; then
  ok "noms de comptes réservés refusés avant toute modification, règle de protection ($(tail -n 3 "$work/unit.log" | grep -o 'Ran [0-9]* tests'))"
else
  fail "contrôles unitaires de forgejo_provision.py"; cat "$work/unit.log"
fi

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

umask 077
"$venv_python" - "$repo" "$work" "$prefix" "$port" "$project" <<'PY'
import copy, json, secrets, sys
import yaml

repo, work, prefix, port, project = sys.argv[1:6]
client = yaml.safe_load(open(f"{repo}/clients/demo/client.yml", encoding="utf-8"))
client["server"]["address"] = "127.0.0.1"
client["maintenance"]["users"] = [
    {"name": "testeur", "full_name": "Testeur", "ssh_public_keys": [open(f"{work}/maintenance_key.pub").read().strip()]}
]
client["backup"] = {**client.get("backup", {}), "repository": f"{prefix}/restic"}
people = client["users"]["people"]
assert [p["id"] for p in people] == ["camille", "lucas"], "the demo client must have camille and lucas"
yaml.safe_dump(client, open(f"{work}/client.yml", "w", encoding="utf-8"), allow_unicode=True)
# Same client without lucas: his accounts must be disabled.
removed = copy.deepcopy(client)
removed["users"]["people"] = [p for p in people if p["id"] != "lucas"]
yaml.safe_dump(removed, open(f"{work}/client-removed.yml", "w", encoding="utf-8"), allow_unicode=True)

names = ["maintenance_sudo_password", "db_root_password", "dokos_admin_password", "entrepreneur_password",
         "restic_password", "forgejo_admin_password"]
data = {"secrets": {name: secrets.token_urlsafe(24) for name in names}}
data["secrets"].update({"restic_env": {}, "tailscale_auth_key": "", "etransactions": {}, "ai_providers": {}})
yaml.safe_dump(data, open(f"{work}/secrets.yml", "w", encoding="utf-8"))

overrides = {
    "dokopilot_forgejo_dir": f"{prefix}/forgejo",
    "dokopilot_forgejo_compose_project": project,
    "dokopilot_etc_dir": f"{prefix}/etc",
    "dokopilot_forgejo_http_port": int(port),
    "dokopilot_forgejo_url": f"http://127.0.0.1:{port}/",
}
yaml.safe_dump(overrides, open(f"{work}/vars.yml", "w", encoding="utf-8"))
# curl configuration with the administrator's login (never on a command line).
with open(f"{work}/admin.curl", "w", encoding="utf-8") as handle:
    handle.write('user = "dokopilot-admin:%s"\n' % data["secrets"]["forgejo_admin_password"])
PY
umask 022
credentials="$prefix/etc/forgejo"

playbook() { # playbook <log name> <client file> <playbook> [extra args...]
  local log="$work/$1.log" client="$2" book="$3"
  shift 3
  (cd "$repo/ansible" && ANSIBLE_CONFIG=ansible.cfg ANSIBLE_FORCE_COLOR=0 "$playbook_bin" -i "$work/inventory.yml" \
      -e "@$client" -e "@$work/secrets.yml" -e "@$work/vars.yml" "$@" "playbooks/$book") > "$log" 2>&1
}
recap() { grep -A1 '^PLAY RECAP' "$work/$1.log" | tail -n 1; }
changed_count() { sed -n 's/^demo[[:space:]]*:.*changed=\([0-9][0-9]*\).*/\1/p' "$work/$1.log" | tail -n 1; }
changed_tasks() { awk '/^TASK \[/ { task = $0 } /^changed: / { print "      " task }' "$work/$1.log"; }
show_failure() { echo "--- fin du journal $1 ---"; tail -n 40 "$work/$1.log"; }
no_change() { # no_change <log name>
  local count
  count="$(changed_count "$1")"
  [[ "$count" == 0 ]] || { echo "      changed=${count:-?} :"; changed_tasks "$1"; return 1; }
}

# --------------------------------------------------------------------------- HTTP and Git helpers

api() { # api <path> : GET as the administrator, JSON on stdout
  curl -fsS -K "$work/admin.curl" -H 'Accept: application/json' "$base/api/v1$1"
}
# A person authenticates with the token of their credential file (what the workstation
# uses); an assistant, which holds no token, with its password (its file has no token).
status_as() { # status_as <account> <path> : HTTP status of a GET as the account
  curl -sS -o /dev/null -w '%{http_code}' -H @<(jq -r 'if .token then "Authorization: token \(.token)"
    else "Authorization: Basic \("\(.user):\(.password)" | @base64)" end' "$credentials/$1.json") "$base/api/v1$2"
}
admin_send() { # admin_send <method> <path> <JSON body> [header] : « <HTTP status> <message> »; answer in $work/answer.json
  local extra=()
  if [[ -n "${4:-}" ]]; then extra=(-H "$4"); fi
  curl -sS -K "$work/admin.curl" -H 'Accept: application/json' -H 'Content-Type: application/json' "${extra[@]}" \
    -X "$1" --data "$3" -o "$work/answer.json" -w '%{http_code}' "$base/api/v1$2"
  printf ' %s' "$(jq -r 'if type == "object" then .message // "" else "" end' "$work/answer.json" 2>/dev/null)"
}
commits() { api "/repos/$org/$1/commits?limit=50" | jq 'length'; }
file_of() { # file_of <repo> <path> : raw content of a file of the default branch
  curl -fsS -K "$work/admin.curl" "$base/api/v1/repos/$org/$1/raw/$(jq -rn --arg v "$2" '$v|@uri')"
}
git_as() { # git_as <account> <git args...> : Git over HTTP as the account (token of a person, password of an assistant)
  local account="$1" store="$work/git-$1.credentials"
  shift
  if [[ ! -f "$store" ]]; then
    jq -r --arg base "$base" '. as $c | $base | sub("://"; "://\($c.user):\($c.token // $c.password)@")' \
      "$credentials/$account.json" > "$store"
    chmod 600 "$store"
  fi
  git -c credential.helper= -c credential.helper="store --file=$store" -c user.name="Test $account" \
    -c user.email="$account@test.invalid" -c init.defaultBranch=main "$@"
}
can_clone() { git_as "$1" clone -q "$base/$org/$2.git" "$work/clones/$1-$2" >/dev/null 2>&1; }
can_push() { # can_push <account> <clone directory>
  local account="$1" dir="$2"
  date +%s%N > "$dir/essai-$account.md"
  git -C "$dir" add . && git -C "$dir" -c user.name=t -c user.email=t@test.invalid commit -qm "Essai de $account" >/dev/null
  git_as "$account" -C "$dir" push -q origin HEAD:main >/dev/null 2>&1
}
force_push() { # force_push <account> <repo> : rewrites the last commit of main in a fresh clone, pushes with --force
  local account="$1" dir="$work/clones/force-$1-$2"
  rm -rf "$dir"
  git_as "$account" clone -q "$base/$org/$2.git" "$dir" >/dev/null 2>&1 || { echo "clonage de $2 impossible"; return 1; }
  git -C "$dir" -c user.name=t -c user.email=t@test.invalid commit -q --amend --allow-empty -m "Réécriture par $account" &&
    git_as "$account" -C "$dir" push --force origin HEAD:main
}
delete_branch() { # delete_branch <account> <clone directory> <branch> : git push origin :<branch>
  git_as "$1" -C "$2" push origin ":$3"
}
mkdir -p "$work/clones"

# --------------------------------------------------------------------------- 1. first run

echo "Installation de Forgejo et du contexte (port $port, $prefix)…"
t0=$(date +%s)
if playbook first "$work/client.yml" context.yml; then
  ok "context.yml (premier passage, $(( $(date +%s) - t0 )) s) : $(recap first)"
else
  fail "context.yml (premier passage)"; show_failure first; exit 1
fi

check "Forgejo répond sur 127.0.0.1:$port seulement" equals \
  "$(docker compose -p "$project" ps --format json | jq -rs 'map(.Publishers[]? | select(.PublishedPort > 0) | .URL) | unique | join(",")')" "127.0.0.1"
check "image Forgejo 17.0.1 rootless figée par empreinte" contains \
  "$(docker compose -p "$project" ps --format json | jq -rs '.[0].Image')" "forgejo:17.0.1-rootless@sha256:"
check "version de Forgejo" contains "$(api /version | jq -r .version)" "17.0.1"
check "sans connexion, l'API ne répond pas" equals "$(curl -sS -o /dev/null -w '%{http_code}' "$base/api/v1/repos/$org/entreprise")" 403
check "sans connexion, l'interface renvoie vers la connexion" contains \
  "$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "$base/explore/repos")" "/user/login"
check "inscription fermée" contains "$(curl -sS -H 'Accept-Language: en' "$base/user/sign_up")" "Registration is disabled"
check ".env de Forgejo en 0600 root" equals "$(stat -c '%a %U' "$prefix/forgejo/.env")" "600 root"

check "organisation $org privée" equals "$(api "/orgs/$org" | jq -r '.visibility')" private
for account in camille lucas assistant-camille assistant-lucas; do
  check "compte $account actif" equals "$(api "/users/$account" | jq -r '[.active, .prohibit_login] | join(" ")')" "true false"
done
check "assistants en comptes restreints" equals "$(api /users/assistant-lucas | jq -r .restricted)" true
check "nom de l'assistant (auteur de ses commits)" equals "$(api /users/assistant-camille | jq -r .full_name)" "Assistant de Camille"

teams="$(api "/orgs/$org/teams")"
team_id() { jq -r --arg n "$1" '.[] | select(.name == $n) | .id' <<<"$teams"; }
members() { api "/teams/$(team_id "$1")/members" | jq -r 'map(.login) | sort | join(" ")'; }
team_repos() { api "/teams/$(team_id "$1")/repos" | jq -r 'map(.name) | sort | join(" ")'; }
check "équipe membres : toutes les personnes et leurs assistants" equals "$(members membres)" "assistant-camille assistant-lucas camille lucas"
check "équipe membres : écriture sur entreprise seulement" equals "$(team_repos membres)" "entreprise"
check "équipe membres : droit d'écriture" equals "$(jq -r '.[] | select(.name == "membres") | .permission' <<<"$teams")" write
check "équipe direction : la dirigeante et son assistant" equals "$(members direction)" "assistant-camille camille"
check "équipe direction : direction seulement" equals "$(team_repos direction)" "direction"
check "perso-camille : camille (admin) et son assistant (écriture), personne d'autre" equals \
  "$(for u in $(api "/repos/$org/perso-camille/collaborators" | jq -r '.[].login' | sort); do
       printf '%s:%s ' "$u" "$(api "/repos/$org/perso-camille/collaborators/$u/permission" | jq -r .role_name)"; done)" \
  "assistant-camille:write camille:admin "
check "perso-lucas : lucas et son assistant" equals \
  "$(api "/repos/$org/perso-lucas/collaborators" | jq -r 'map(.login) | sort | join(" ")')" "assistant-lucas lucas"

for name in entreprise direction perso-camille perso-lucas; do
  info="$(api "/repos/$org/$name")"
  check "dépôt $name privé, non vide, branche main" equals "$(jq -r '[.private, .empty, .default_branch] | join(" ")' <<<"$info")" "true false main"
  check "dépôt $name : un seul commit, de « DokoPilot »" equals \
    "$(api "/repos/$org/$name/commits?limit=50" | jq -r 'map(.commit.author.name) | join(",")')" "DokoPilot"
  # push enabled, no allowlist, no protected file, no administrator exemption (D-32)
  check "dépôt $name : branche main protégée, poussée ouverte aux rédacteurs, aucun fichier protégé" equals \
    "$(api "/repos/$org/$name/branch_protections" | jq -r 'map(select(.rule_name == "main"))
       | map([.enable_push, .enable_push_whitelist, .protected_file_patterns == "", .apply_to_admins] | join(" ")) | join(",")')" \
    "true false true true"
done
check "miroirs désactivés : pas de miroir poussé, même par l'administrateur du dépôt" equals \
  "$(admin_send POST "/repos/$org/perso-lucas/push_mirrors" '{"remote_address":"https://example.com/copie.git","interval":"8h"}' 'Sudo: lucas')" \
  "400 Mirror feature is disabled"
check "app.ini : [mirror] ENABLED = false" equals \
  "$(docker compose --project-directory "$prefix/forgejo" -f "$prefix/forgejo/compose.yaml" exec -T forgejo \
       sed -n '/^\[mirror\]/,/^\[/p' /var/lib/gitea/custom/conf/app.ini | grep -c '^ENABLED *= *false$')" 1

tree="$(api "/repos/$org/entreprise/git/trees/main?recursive=true&per_page=1000" | jq -r '.tree[] | select(.type == "blob") | .path')"
for path in AGENTS.md CLAUDE.md .gitignore README.md entreprise.md 00-Boite-de-reception/README.md 10-Sources/README.md \
  20-Notes-permanentes/README.md 30-Cartes/README.md 40-Projets/README.md 60-Donnees/dokos.md 60-Donnees/messagerie.md \
  60-Donnees/agenda.md 80-Archive/README.md 90-Modeles/note.md 90-Modeles/source.md 90-Modeles/carte.md 90-Modeles/projet.md \
  90-Modeles/fonction.md 90-Modeles/compte-rendu.md 90-Modeles/decision.md; do
  check "entreprise contient $path" contains "$tree" "$path"
done
for function in direction commercial achats comptabilite-finance production rh administratif-juridique communication; do
  check "fonction $function" contains "$tree" "50-Fonctions/$function/README.md"
done
check "contenu de démonstration (projet, carte, notes permanentes)" equals \
  "$(grep -cE '^(40-Projets/mobilier-restaurant-le-chene-vert|30-Cartes/carte-du-devis-au-paiement|20-Notes-permanentes/(controler-les-factures-fournisseurs|factures-clients-a-30-jours))\.md$' <<<"$tree")" 4
check "aucun modèle .j2 déposé" equals "$(grep -c '\.j2$' <<<"$tree" || true)" 0
agents="$(file_of entreprise AGENTS.md)"
check "AGENTS.md : les huit règles" equals "$(grep -c '^### [1-8]\. ' <<<"$agents")" 8
check "AGENTS.md : règle d'or et jamais de git push" contains "$agents" "**Jamais de \`git push\`**"
check "AGENTS.md : contextes Bureau et Atelier" contains "$agents" "| **Atelier** |"
check "AGENTS.md : rendu complet (ni Jinja ni en-tête de licence)" equals "$(grep -cE '\{\{|\{%|SPDX' <<<"$agents" || true)" 0
check "CLAUDE.md importe AGENTS.md" equals "$(file_of entreprise CLAUDE.md)" "@AGENTS.md"
check ".gitignore exclut direction/, perso/ et le harnais" equals \
  "$(file_of entreprise .gitignore | grep -cE '^/(direction|perso|opencode\.json|\.mcp\.json|\.claude|\.opencode)/?$|^/\.agents/skills/dokopilot-\*/$')" 7
check "fiche entreprise : l'Atelier Démo et ses personnes" equals \
  "$(file_of entreprise entreprise.md | grep -cE 'Lons-le-Saunier|^\| Camille Martin \| Dirigeant|^\| Lucas Bernard \| Membre')" 3
check "fiche entreprise : propriétés de note" contains "$(file_of entreprise entreprise.md | sed -n 1,10p)" "sensibilite: interne"
check "fonction achats renseignée (démonstration)" contains "$(file_of entreprise 50-Fonctions/achats/README.md)" "Scierie Démo du Jura"
check "fonction commercial en attente (« à compléter »)" contains "$(file_of entreprise 50-Fonctions/commercial/README.md)" "à compléter"
check "direction : AGENTS.md de confidentialité renforcée" contains "$(file_of direction AGENTS.md)" "Confidentialité renforcée"
for folder in rh remunerations strategie juridique; do
  check "direction contient $folder/" contains "$(api "/repos/$org/direction/contents/$folder" | jq -r '.[].name')" "README.md"
done
check "perso-camille : préférences de Camille" contains "$(file_of perso-camille AGENTS.md)" "Vous assistez **Camille Martin** (dirigeant de l'entreprise Atelier Démo)."
check "perso-lucas : préférences de Lucas, sans accès à Dokos" contains "$(file_of perso-lucas AGENTS.md)" "pas d'accès de l'agent à Dokos"
check "perso : mémoire vive (500 mots au plus)" contains "$(file_of perso-lucas memoire-vive.md)" "500 mots au plus"
check "perso : boîte de réception et projets" equals \
  "$(api "/repos/$org/perso-lucas/git/trees/main?recursive=true" | jq -r '.tree[].path' | grep -cE '^(00-Boite-de-reception|40-Projets)/README\.md$')" 2

check "dossier des identifiants en 0700 root" equals "$(stat -c '%a %U' "$credentials")" "700 root"
for account in camille lucas; do
  file="$credentials/$account.json"
  check "identifiants de $account : 0600 root, user, password, token" equals \
    "$(stat -c '%a %U' "$file") $(jq -r '[.user, (.password | length > 12), (.token | length == 40)] | join(" ")' "$file")" \
    "600 root $account true true"
done
for account in assistant-camille assistant-lucas; do
  file="$credentials/$account.json"
  check "identifiants de $account : 0600 root, user, password, sans jeton" equals \
    "$(stat -c '%a %U' "$file") $(jq -r '[.user, (.password | length > 12), has("token")] | join(" ")' "$file")" \
    "600 root $account true false"
  check "$account ne détient aucun jeton dans Forgejo" equals "$(api "/users/$account/tokens" | jq 'length')" 0
done
check "jeton de lucas : écriture des dépôts seulement, limité à ses dépôts" equals \
  "$(api /users/lucas/tokens | jq -r '.[] | select(.name == "dokopilot") | [(.scopes | join(",")), (.repositories | map(.name) | sort | join(","))] | join(" ")')" \
  "write:repository entreprise,perso-lucas"
check "jeton du compte de la dirigeante : entreprise, direction, perso-camille" equals \
  "$(api /users/camille/tokens | jq -r '.[] | select(.name == "dokopilot") | .repositories | map(.name) | sort | join(",")')" \
  "direction,entreprise,perso-camille"

# --------------------------------------------------------------------------- 2. second run, checks

if playbook second "$work/client.yml" context.yml; then ok "context.yml (second passage) : $(recap second)"; else fail "context.yml (second passage)"; show_failure second; fi
check "second passage sans changement (idempotent)" no_change second
for name in entreprise direction perso-camille perso-lucas; do
  check "dépôt $name : toujours un seul commit" equals "$(commits "$name")" 1
done
check "contexte jamais réécrit : dépôts laissés tels quels" contains "$(tr -s ' \n' ' ' < "$work/second.log")" \
  "Laissés tels quels (déjà remplis, jamais écrasés) : direction, entreprise, perso-camille, perso-lucas"

if playbook check "$work/client.yml" check.yml --tags forgejo; then
  ok "check.yml --tags forgejo : $(recap check)"
else
  fail "check.yml --tags forgejo"; show_failure check
fi
check "contrôles Forgejo conformes" contains "$(cat "$work/check.log")" "Résultat : tout est conforme"

# --------------------------------------------------------------------------- 3. rights, with Git

check "assistant-camille clone direction" can_clone assistant-camille direction
check "assistant-camille pousse dans direction" can_push assistant-camille "$work/clones/assistant-camille-direction"
check "assistant-lucas (membre) ne lit pas direction (Git)" refused can_clone assistant-lucas direction
check "assistant-lucas ne lit pas direction (API)" equals "$(status_as assistant-lucas "/repos/$org/direction")" 404
check "assistant-lucas clone entreprise" can_clone assistant-lucas entreprise
check "assistant-lucas pousse dans entreprise" can_push assistant-lucas "$work/clones/assistant-lucas-entreprise"
check "assistant-camille clone perso-camille" can_clone assistant-camille perso-camille
check "assistant-lucas ne pousse pas dans perso-camille" refused can_push assistant-lucas "$work/clones/assistant-camille-perso-camille"
check "assistant-lucas ne lit pas perso-camille" refused can_clone assistant-lucas perso-camille
check "lucas clone perso-lucas" can_clone lucas perso-lucas
check "lucas pousse dans perso-lucas" can_push lucas "$work/clones/lucas-perso-lucas"
check "lucas ne lit pas direction" equals "$(status_as lucas "/repos/$org/direction")" 404
check "camille clone direction" can_clone camille direction

# Protected default branch (D-32): normal pushes pass, rewriting or deleting main does not,
# whoever asks (a team writer, an assistant, the administrator of a perso repository).
# (Nothing new is pushed to entreprise here: the clone of assistant-lucas pushes there again below.)
check "camille clone perso-camille" can_clone camille perso-camille
check "camille pousse dans perso-camille (branche main protégée)" can_push camille "$work/clones/camille-perso-camille"
check "camille ne réécrit pas main d'entreprise (git push --force)" \
  rejected_with "branch main is protected from force push" force_push camille entreprise
check "assistant-camille ne réécrit pas main de direction" \
  rejected_with "branch main is protected from force push" force_push assistant-camille direction
check "lucas, administrateur de perso-lucas, ne réécrit pas main" \
  rejected_with "branch main is protected from force push" force_push lucas perso-lucas
check "lucas ne supprime pas main de perso-lucas (git push origin :main)" \
  rejected_with "pre-receive hook declined" delete_branch lucas "$work/clones/lucas-perso-lucas" main
check "lucas ne supprime pas main par l'API (avec son jeton)" contains \
  "$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE -H @<(jq -r '"Authorization: token \(.token)"' "$credentials/lucas.json") \
     "$base/api/v1/repos/$org/perso-lucas/branches/main")" 403
check "le jeton de lucas ne retire pas la protection (droits d'administration exigés)" contains \
  "$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE -H @<(jq -r '"Authorization: token \(.token)"' "$credentials/lucas.json") \
     "$base/api/v1/repos/$org/perso-lucas/branch_protections/main")" 403
# Forgejo already refuses deleting the default branch; the protection also covers main once
# another branch has become the default one (possible for the administrator of the repository).
check "lucas pousse une autre branche dans perso-lucas" \
  quietly git_as lucas -C "$work/clones/lucas-perso-lucas" push -q origin HEAD:essai
check "branche par défaut de perso-lucas : essai" contains "$(admin_send PATCH "/repos/$org/perso-lucas" '{"default_branch":"essai"}')" 200
check "main, devenue une branche ordinaire, reste protégée contre la suppression" \
  rejected_with "branch main is protected from deletion" delete_branch lucas "$work/clones/lucas-perso-lucas" main
check "branche par défaut de perso-lucas : main de nouveau" contains "$(admin_send PATCH "/repos/$org/perso-lucas" '{"default_branch":"main"}')" 200
check "une branche non protégée se supprime normalement" quietly delete_branch lucas "$work/clones/lucas-perso-lucas" essai

# --------------------------------------------------------------------------- 4. copy and restore of the data volume

fcompose() { docker compose --project-directory "$prefix/forgejo" -f "$prefix/forgejo/compose.yaml" "$@"; }
before="$(commits entreprise)"
fcompose stop forgejo >/dev/null 2>&1
fcompose run --rm --no-deps -T --entrypoint tar forgejo -C /var/lib/gitea -cf - . > "$work/forgejo-data.tar" 2>> "$work/volume.log"
fcompose start forgejo >/dev/null 2>&1
check "copie du volume : base SQLite, app.ini et dépôts" equals \
  "$(tar -tf "$work/forgejo-data.tar" | grep -cE '^\./(data/gitea\.db|custom/conf/app\.ini|git/repositories/demo/entreprise\.git/HEAD)$')" 3
until curl -fsS "$base/api/healthz" >/dev/null 2>&1; do sleep 1; done
check "assistant-lucas pousse encore dans entreprise (après la copie)" can_push assistant-lucas "$work/clones/assistant-lucas-entreprise"
fcompose stop forgejo >/dev/null 2>&1
fcompose run --rm --no-deps -T -v "$work/forgejo-data.tar:/backup/forgejo-data.tar:ro" --entrypoint sh forgejo \
  -c 'find /var/lib/gitea -mindepth 1 -delete && tar -C /var/lib/gitea -xf /backup/forgejo-data.tar' 2>> "$work/volume.log"
fcompose start forgejo >/dev/null 2>&1
until curl -fsS "$base/api/healthz" >/dev/null 2>&1; do sleep 1; done
check "restauration : le dépôt revient à l'état de la copie" equals "$(commits entreprise)" "$before"
check "restauration : les jetons restent valables" equals "$(status_as camille "/repos/$org/direction")" 200

# --------------------------------------------------------------------------- 5. drift detected and repaired

# A protection removed, another weakened (in the web interface, an administrator of the
# repository could do it), and an assistant token with its old-format credential file, as an
# earlier version left them: check.yml reports each one, the next run repairs them.
check "protection d'entreprise retirée (simulation)" contains "$(admin_send DELETE "/repos/$org/entreprise/branch_protections/main" '{}')" 204
check "protection de perso-camille affaiblie (simulation)" contains \
  "$(admin_send PATCH "/repos/$org/perso-camille/branch_protections/main" '{"enable_push":false}')" 200
check "ancien jeton de assistant-camille (simulation)" contains \
  "$(admin_send POST /users/assistant-camille/tokens "{\"name\":\"dokopilot\",\"scopes\":[\"write:repository\"],\"repositories\":[{\"owner\":\"$org\",\"name\":\"entreprise\"}]}")" 201
jq --slurpfile answer "$work/answer.json" '. + {token: $answer[0].sha1}' "$credentials/assistant-camille.json" > "$work/old-format.json"
install -m 600 -o root -g root "$work/old-format.json" "$credentials/assistant-camille.json"
rm -f "$work/old-format.json" "$work/answer.json"
if playbook drift "$work/client.yml" check.yml --tags forgejo; then
  fail "check.yml --tags forgejo aurait dû échouer (protections et jeton d'assistant)"
else
  drift="$(tr -s ' \n' ' ' < "$work/drift.log")"
  check "contrôle : protection retirée signalée" contains "$drift" "branche main de $org/entreprise non protégée"
  check "contrôle : protection affaiblie signalée" contains "$drift" "protection de la branche main de $org/perso-camille modifiée (enable_push)"
  check "contrôle : jeton d'assistant signalé" contains "$drift" "assistant-camille détient des jetons (dokopilot)"
  check "contrôle : fichier d'assistant avec jeton signalé" contains "$drift" "assistant-camille.json contient un jeton"
fi

# --------------------------------------------------------------------------- 6. a person removed

if playbook removed "$work/client-removed.yml" context.yml; then ok "context.yml sans lucas : $(recap removed)"; else fail "context.yml sans lucas"; show_failure removed; fi
for name in entreprise perso-camille; do
  check "protection de main rétablie : $name" equals \
    "$(api "/repos/$org/$name/branch_protections" | jq -r 'map(select(.rule_name == "main"))
       | map([.enable_push, .enable_push_whitelist, .protected_file_patterns == "", .apply_to_admins] | join(" ")) | join(",")')" \
    "true false true true"
done
check "ancien jeton de assistant-camille supprimé" equals "$(api /users/assistant-camille/tokens | jq 'length')" 0
check "fichier de assistant-camille réécrit sans jeton, mot de passe gardé" equals \
  "$(jq -r '[.user, has("token"), (.password | length > 12)] | join(" ")' "$credentials/assistant-camille.json")" "assistant-camille false true"
check "assistant-camille pousse encore dans direction (mot de passe gardé)" can_push assistant-camille "$work/clones/assistant-camille-direction"
for account in lucas assistant-lucas; do
  check "compte $account désactivé" equals "$(api "/users/$account" | jq -r .prohibit_login)" true
  check "identifiants de $account supprimés" test ! -e "$credentials/$account.json"
done
check "équipe membres sans lucas" equals "$(members membres)" "assistant-camille camille"
check "dépôt perso-lucas conservé" equals "$(api "/repos/$org/perso-lucas" | jq -r '.name')" perso-lucas
check "l'ancien jeton de lucas est refusé" refused can_clone lucas entreprise
check "assistant-lucas ne pousse plus" refused can_push assistant-lucas "$work/clones/assistant-lucas-entreprise"
if playbook removed-again "$work/client-removed.yml" context.yml; then ok "context.yml sans lucas, second passage : $(recap removed-again)"; else fail "context.yml sans lucas, second passage"; show_failure removed-again; fi
check "second passage sans lucas sans changement" no_change removed-again
if playbook check-removed "$work/client-removed.yml" check.yml --tags forgejo; then
  ok "check.yml --tags forgejo sans lucas : $(recap check-removed)"
else
  fail "check.yml --tags forgejo sans lucas"; show_failure check-removed
fi

# --------------------------------------------------------------------------- result

echo
if (( failures > 0 )); then
  echo "$failures contrôle(s) en échec ($(( $(date +%s) - started )) s)."
  exit 1
fi
echo "Tous les contrôles sont passés ($(( $(date +%s) - started )) s)."
