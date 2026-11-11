#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Forgejo provisioning for DokoPilot, run on the server by the forgejo and context roles.

    forgejo_provision.py <step>        the payload is a JSON object on stdin

Steps (C-2 and C-3 of docs/plan-v0.2.md, D-30 and D-32 of docs/decisions.md):
  ensure  administrator, organisation, repositories and the protection of their default
          branch, accounts, teams, collaborators, the people's tokens and the credential
          files; disables the accounts that are no longer configured and deletes their
          credential files (repositories are never deleted).
  fill    pushes the rendered context as the first commit of each EMPTY repository; a
          repository that already has a commit is never touched.
  check   read-only checks for check.yml.

The ensure and check steps first refuse, before any call to Forgejo, an account name that
Forgejo reserves or that collides with the administrator, the organisation or the
assistants (check_logins).

The payload holds the administrator's password: it arrives on stdin, never on a command
line. The output is one JSON object, {"changed": ..., ...} or {"failed": true, "msg": ...}
with a French message; it never contains a password or a token. Credential files are
written here, root only: <credentials_dir>/<person>.json = {"user", "password", "token"}
(the token is what the person's workstation uses) and <credentials_dir>/<assistant>.json =
{"user", "password"} (an assistant holds no token: nothing uses one, D-32).
Standard library only.
"""

from __future__ import annotations

import base64
import configparser
import json
import os
import re
import secrets
import stat
import string
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PAGE = 50
TIMEOUT = 30

# User names that Forgejo 17.0.1 reserves: reservedUsernames and reservedUserPatterns of
# models/user/user.go at tag v17.0.1, compared in lower case as models/db/name.go
# (IsUsableName) does. Each name was checked against the admin API of 17.0.1 (« name is
# reserved », HTTP 422). Update this list with the image (docs/exploitation.md).
RESERVED_USER_NAMES = frozenset({
    ".", "..", "-", ".well-known", "api", "metrics", "v2", "assets", "attachments", "avatar",
    "avatars", "repo-avatars", "captcha", "login", "org", "repo", "user", "admin", "explore",
    "issues", "pulls", "milestones", "notifications", "report_abuse", "favicon.ico",
    "manifest.json", "robots.txt", "sitemap.xml", "ssh_info", "swagger.v1.json", "ghost",
    "gitea-actions", "forgejo-actions", "actor",
})
RESERVED_USER_SUFFIXES = (".keys", ".gpg", ".rss", ".atom", ".png")
# Characters Forgejo accepts in a user name (modules/validation/helpers.go of v17.0.1,
# IsValidUsername, dots allowed by default): no double nor final « - », « . » or « _ ».
VALID_USER_NAME = re.compile(r"^[0-9A-Za-z][-.\w]*$", re.ASCII)
INVALID_USER_NAME = re.compile(r"[-._]{2,}|[-._]$")

# Protection of the default branch of every context repository (D-32). Forgejo refuses a
# force push and the deletion of a protected branch to everyone, administrators included,
# before any other rule (routers/private/hook_pre_receive.go of v17.0.1, « enforce
# absolutely »). Pushing stays open to everyone who can write (enable_push without an
# allowlist). No protected file pattern: it would also block the dirigeant's own edits of
# AGENTS.md (D-32). Fields of CreateBranchProtectionOption and EditBranchProtectionOption,
# /api/swagger of 17.0.1.
PROTECTION = {
    "enable_push": True,
    "enable_push_whitelist": False,
    "require_signed_commits": False,
    "protected_file_patterns": "",
    "apply_to_admins": True,
}


class Failure(Exception):
    """A provisioning error, with a French message for the integrator."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        return None


class Api:
    """Minimal client of the Forgejo REST API (/api/v1)."""

    def __init__(self, base_url: str, user: str = "", password: str = "") -> None:
        self.root = base_url.rstrip("/")
        self.base = self.root + "/api/v1"
        self.user = user
        self.basic = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode() if user else ""
        self.opener = urllib.request.build_opener(_NoRedirect)

    def raw(self, method: str, url: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any, dict[str, str]]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=TIMEOUT) as response:
                status, payload, response_headers = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            status, payload, response_headers = error.code, error.read(), dict(error.headers or {})
        except (urllib.error.URLError, OSError) as error:
            raise Failure(f"Forgejo injoignable sur {self.root} : {error}") from error
        try:
            decoded = json.loads(payload) if payload else None
        except ValueError:
            decoded = payload.decode(errors="replace")[:300]
        return status, decoded, response_headers

    def call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200, 201, 204),
             token: str = "", sudo: str = "", auth: bool = True) -> tuple[int, Any]:
        headers = {}
        if token:
            headers["Authorization"] = "token " + token
        elif auth and self.basic:
            headers["Authorization"] = self.basic
        if sudo:
            headers["Sudo"] = sudo
        status, data, _ = self.raw(method, self.base + path, body, headers)
        if status not in ok:
            detail = data.get("message", "") if isinstance(data, dict) else str(data or "")
            raise Failure(f"Forgejo a refusé {method} {path} ({status}) : {detail}".strip())
        return status, data

    def get(self, path: str, missing_ok: bool = False) -> Any:
        status, data = self.call("GET", path, ok=(200, 404) if missing_ok else (200,))
        return None if status == 404 else data

    def paged(self, path: str) -> list[Any]:
        items: list[Any] = []
        separator = "&" if "?" in path else "?"
        page = 1
        while True:
            batch = self.get(f"{path}{separator}page={page}&limit={PAGE}") or []
            items.extend(batch)
            if len(batch) < PAGE:
                return items
            page += 1


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def random_password(length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


# ----------------------------------------------------------------------------- container


def compose(payload: dict[str, Any], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Runs a command in the Forgejo container. Never pass a secret in args."""
    spec = payload["compose"]
    argv = ["docker", "compose", "--project-directory", spec["dir"], "-f", spec["dir"] + "/compose.yaml",
            "exec", "-T", spec["service"], *args]
    result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120)
    if check and result.returncode != 0:
        # The output of these commands never contains a secret (no --password option is used).
        raise Failure(f"Commande Forgejo en échec ({' '.join(args[:4])}) : {(result.stderr or result.stdout).strip()[-400:]}")
    return result


def wait_ready(api: Api, seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            status, data, _ = api.raw("GET", api.root + "/api/healthz")
            if status == 200 and isinstance(data, dict) and data.get("status") == "pass":
                return
        except Failure:
            pass
        if time.monotonic() > deadline:
            raise Failure(f"Forgejo ne répond pas sur {api.root}/api/healthz")
        time.sleep(2)


# ----------------------------------------------------------------------------- model


def complete(payload: dict[str, Any]) -> dict[str, Any]:
    """Adds accounts, repos, teams and collaborators, derived from the people (C-2)."""
    settings = payload["settings"]
    people = payload["people"]
    dirigeants = [person for person in people if person.get("role") == "dirigeant"]
    if len(dirigeants) != 1:
        raise Failure("users.people doit contenir exactement un dirigeant")
    prefix = settings["assistant_prefix"]
    accounts: list[dict[str, Any]] = []
    collaborators: dict[str, dict[str, str]] = {}
    members: list[str] = []

    def assistant_of(person: dict[str, Any]) -> list[str]:
        return [prefix + person["id"]] if person.get("assistant") else []

    for person in people:
        accounts.append({
            "user": person["id"], "email": person["email"], "kind": "person", "restricted": False,
            "full_name": f"{person.get('first_name', '')} {person.get('last_name', '')}".strip() or person["id"],
            "must_change_password": bool(settings["people_must_change_password"])})
        rights = {person["id"]: "admin"}
        for assistant in assistant_of(person):
            if not person.get("assistant_email"):
                raise Failure(f"adresse de l'assistant de {person['id']} absente (users.people[].assistant_email)")
            accounts.append({
                "user": assistant, "email": person["assistant_email"], "kind": "assistant",
                "full_name": settings["assistant_full_name"].format(first_name=person.get("first_name") or person["id"]),
                "restricted": bool(settings["assistants_restricted"]), "must_change_password": False})
            rights[assistant] = "write"
        members += [person["id"], *assistant_of(person)]
        collaborators[f"perso-{person['id']}"] = rights
    descriptions = settings["repo_descriptions"]
    repos = [{"name": "entreprise", "description": descriptions["entreprise"]}]
    teams = [{"name": settings["team_members"], "permission": "write", "units": settings["team_units"],
              "members": members, "repos": ["entreprise"],
              "description": "Toutes les personnes et leurs assistants : écriture sur entreprise"}]
    if payload["direction"]:
        repos.append({"name": "direction", "description": descriptions["direction"]})
        teams.append({"name": settings["team_direction"], "permission": "write", "units": settings["team_units"],
                      "members": [dirigeants[0]["id"], *assistant_of(dirigeants[0])], "repos": ["direction"],
                      "description": "Le dirigeant et son assistant : écriture sur direction"})
    for person in people:
        name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip() or person["id"]
        repos.append({"name": f"perso-{person['id']}", "description": descriptions["perso"].format(name=name)})
    users = [account["user"].lower() for account in accounts]
    duplicates = sorted({user for user in users if users.count(user) > 1})
    if duplicates:
        raise Failure(f"comptes Forgejo en double : {', '.join(duplicates)}")
    check_logins(payload, accounts)
    return {**payload, "accounts": accounts, "repos": repos, "teams": teams, "collaborators": collaborators}


def check_logins(payload: dict[str, Any], accounts: list[dict[str, Any]]) -> None:
    """Refuses, before any change, a login Forgejo would refuse or that would collide.

    The config role refuses these person ids already; this is the last line of defence,
    with the reserved names of the running version (RESERVED_USER_NAMES).
    """
    admin = payload["admin"]["user"].lower()
    org = payload["org"]["name"].lower()
    prefix = payload["settings"]["assistant_prefix"].lower()
    problems = []
    if org in RESERVED_USER_NAMES or org.endswith(RESERVED_USER_SUFFIXES) or org == admin:
        # Organisations and accounts share one namespace in Forgejo.
        raise Failure(f"identifiant du client refusé, Forgejo n'a pas été modifié : « {payload['org']['name']} » est un nom "
                      "réservé par Forgejo et ne peut pas nommer l'organisation. Choisissez un autre client.id (client.yml).")
    for account in accounts:
        login = account["user"]
        lower = login.lower()
        if lower == admin:
            problems.append(f"« {login} » est le compte d'administration de Forgejo")
        elif lower == org:
            problems.append(f"« {login} » est le nom de l'organisation (l'identifiant du client)")
        elif account["kind"] == "person" and prefix and lower.startswith(prefix):
            problems.append(f"« {login} » commence par « {prefix} », réservé aux comptes des assistants")
        elif lower in RESERVED_USER_NAMES or lower.endswith(RESERVED_USER_SUFFIXES):
            problems.append(f"« {login} » est un nom réservé par Forgejo")
        elif not VALID_USER_NAME.match(login) or INVALID_USER_NAME.search(login):
            problems.append(f"« {login} » contient des caractères refusés par Forgejo "
                            "(lettres, chiffres et tirets, sans tiret double ni final)")
    if problems:
        raise Failure("identifiants refusés, Forgejo n'a pas été modifié : " + " ; ".join(problems)
                      + ". Choisissez un autre id dans users.people (client.yml).")


# ----------------------------------------------------------------------------- ensure


class Run:
    def __init__(self) -> None:
        self.changes: list[str] = []

    def note(self, message: str) -> None:
        self.changes.append(message)


def ensure_admin(payload: dict[str, Any], api: Api, run: Run) -> None:
    """The administrator exists, is a site administrator and has the password of the secrets."""
    admin = payload["admin"]
    status, me = api.call("GET", "/user", ok=(200, 401, 403))
    if status == 200 and isinstance(me, dict) and me.get("login") == admin["user"] and me.get("is_admin"):
        return
    listing = compose(payload, "forgejo", "admin", "user", "list").stdout
    names = {line.split()[1] for line in listing.splitlines()[1:] if len(line.split()) > 1}
    if admin["user"] not in names:
        # The random password is replaced just below; the output is discarded.
        compose(payload, "forgejo", "admin", "user", "create", "--admin", "--username", admin["user"],
                "--email", admin["email"], "--random-password", "--random-password-length", "32",
                "--must-change-password=false")
        run.note(f"administrateur {admin['user']} créé")
    else:
        run.note(f"mot de passe de l'administrateur {admin['user']} réappliqué")
    token_name = "dokopilot-bootstrap-" + secrets.token_hex(4)
    token = compose(payload, "forgejo", "admin", "user", "generate-access-token", "--username", admin["user"],
                    "--token-name", token_name, "--scopes", "write:admin", "--raw").stdout.strip().splitlines()[-1].strip()
    try:
        api.call("PATCH", f"/admin/users/{quote(admin['user'])}", token=token, body={
            "login_name": admin["user"], "source_id": 0, "password": admin["password"], "email": admin["email"],
            "must_change_password": False, "admin": True, "prohibit_login": False, "active": True})
        status, me = api.call("GET", "/user", ok=(200, 401, 403))
        if status != 200:
            raise Failure(f"connexion de {admin['user']} refusée après la mise à jour de son mot de passe ({status}) ; "
                          "ce compte ne doit pas avoir de double authentification : l'installation s'en sert")
    finally:
        # The administration token does not outlive this call. Forgejo manages tokens with the
        # password only: when the password could not be set, the deletion is refused here, and
        # the next successful run removes the token (every token of this prefix goes).
        try:
            for stale in api.paged(f"/users/{quote(admin['user'])}/tokens"):
                if str(stale.get("name", "")).startswith("dokopilot-bootstrap-"):
                    api.call("DELETE", f"/users/{quote(admin['user'])}/tokens/{stale['id']}", ok=(204, 404))
        except Failure:
            pass


def ensure_org(payload: dict[str, Any], api: Api, run: Run) -> None:
    org = payload["org"]
    current = api.get(f"/orgs/{quote(org['name'])}", missing_ok=True)
    if current is None:
        api.call("POST", "/orgs", body={"username": org["name"], "full_name": org["full_name"], "visibility": "private",
                                        "repo_admin_change_team_access": False})
        run.note(f"organisation {org['name']} créée")
    elif current.get("visibility") != "private" or current.get("full_name") != org["full_name"]:
        api.call("PATCH", f"/orgs/{quote(org['name'])}", body={"full_name": org["full_name"], "visibility": "private"})
        run.note(f"organisation {org['name']} mise à jour (privée)")


def ensure_repos(payload: dict[str, Any], api: Api, run: Run) -> None:
    org = payload["org"]["name"]
    for repo in payload["repos"]:
        current = api.get(f"/repos/{quote(org)}/{quote(repo['name'])}", missing_ok=True)
        if current is None:
            api.call("POST", f"/orgs/{quote(org)}/repos", body={
                "name": repo["name"], "description": repo["description"], "private": True, "auto_init": False,
                "default_branch": payload["default_branch"]})
            run.note(f"dépôt {org}/{repo['name']} créé")
        elif not current.get("private"):
            api.call("PATCH", f"/repos/{quote(org)}/{quote(repo['name'])}", body={"private": True})
            run.note(f"dépôt {org}/{repo['name']} rendu privé")


def protection_differences(current: dict[str, Any]) -> list[str]:
    return [key for key, value in PROTECTION.items() if current.get(key) != value]


def ensure_protections(payload: dict[str, Any], api: Api, run: Run) -> None:
    """The default branch of every context repository is protected (D-32).

    The rule may exist before the branch: it is created on the empty repository, before the
    first commit of the context role (which pushes normally, as the administrator).
    """
    org = payload["org"]["name"]
    branch = payload["default_branch"]
    for repo in payload["repos"]:
        base = f"/repos/{quote(org)}/{quote(repo['name'])}/branch_protections"
        current = api.get(f"{base}/{quote(branch)}", missing_ok=True)
        if current is None:
            api.call("POST", base, body={"rule_name": branch, **PROTECTION})
            run.note(f"branche {branch} de {repo['name']} protégée (ni réécriture ni suppression)")
        elif differences := protection_differences(current):
            api.call("PATCH", f"{base}/{quote(branch)}", body=PROTECTION)
            run.note(f"protection de la branche {branch} de {repo['name']} rétablie ({', '.join(differences)})")


def ensure_accounts(payload: dict[str, Any], api: Api, run: Run) -> dict[str, str]:
    """Creates or updates the configured accounts; returns the passwords of the new ones."""
    created: dict[str, str] = {}
    for account in payload["accounts"]:
        user = account["user"]
        # Names colliding with the organisation were refused by check_logins, before any change.
        current = api.get(f"/users/{quote(user)}", missing_ok=True)
        if current is None:
            password = random_password(payload["password_length"])
            api.call("POST", "/admin/users", body={
                "username": user, "login_name": user, "source_id": 0, "email": account["email"],
                "full_name": account["full_name"], "password": password,
                "must_change_password": account["must_change_password"], "restricted": account["restricted"],
                "send_notify": False, "visibility": "private"})
            created[user] = password
            run.note(f"compte {user} créé")
            continue
        wanted = {"email": account["email"], "full_name": account["full_name"], "restricted": account["restricted"],
                  "prohibit_login": False, "active": True}
        # Only dokopilot-admin administers Forgejo: an administrator reads every repository.
        if any(current.get(key) != value for key, value in wanted.items()) or current.get("is_admin"):
            api.call("PATCH", f"/admin/users/{quote(user)}", body={"login_name": user, "source_id": 0, "admin": False, **wanted})
            run.note(f"compte {user} mis à jour"
                     + (" (droits d'administration de Forgejo retirés)" if current.get("is_admin") else "")
                     + (f" (RÉACTIVÉ : c'est le compte d'une personne retirée auparavant ; il retrouve le dépôt perso de « {user} »"
                        " et son historique. Pour une autre personne, choisissez un autre id)" if current.get("prohibit_login") else ""))
    return created


def ensure_teams(payload: dict[str, Any], api: Api, run: Run) -> set[str]:
    """Teams with exactly the configured members and repositories; returns removed members."""
    org = payload["org"]["name"]
    removed: set[str] = set()
    existing = {team["name"]: team for team in api.paged(f"/orgs/{quote(org)}/teams")}
    for spec in payload["teams"]:
        units_map = {unit: spec["permission"] for unit in spec["units"]}
        body = {"name": spec["name"], "description": spec["description"], "permission": spec["permission"],
                "units": spec["units"], "units_map": units_map, "includes_all_repositories": False,
                "can_create_org_repo": False}
        team = existing.get(spec["name"])
        if team is None:
            _, team = api.call("POST", f"/orgs/{quote(org)}/teams", body=body)
            run.note(f"équipe {spec['name']} créée")
        elif (team.get("permission") != spec["permission"] or team.get("units_map") != units_map
              or team.get("includes_all_repositories") or team.get("can_create_org_repo")
              or (team.get("description") or "") != spec["description"]):
            api.call("PATCH", f"/teams/{team['id']}", body=body)
            run.note(f"équipe {spec['name']} mise à jour")
        team_id = team["id"]
        members = {member["login"] for member in api.paged(f"/teams/{team_id}/members")}
        for user in sorted(set(spec["members"]) - members):
            api.call("PUT", f"/teams/{team_id}/members/{quote(user)}")
            run.note(f"{user} ajouté à l'équipe {spec['name']}")
        for user in sorted(members - set(spec["members"])):
            api.call("DELETE", f"/teams/{team_id}/members/{quote(user)}")
            removed.add(user)
            run.note(f"{user} retiré de l'équipe {spec['name']}")
        repos = {repo["name"] for repo in api.paged(f"/teams/{team_id}/repos")}
        for name in sorted(set(spec["repos"]) - repos):
            api.call("PUT", f"/teams/{team_id}/repos/{quote(org)}/{quote(name)}")
            run.note(f"dépôt {name} donné à l'équipe {spec['name']}")
        for name in sorted(repos - set(spec["repos"])):
            api.call("DELETE", f"/teams/{team_id}/repos/{quote(org)}/{quote(name)}")
            run.note(f"dépôt {name} retiré de l'équipe {spec['name']}")
    return removed


def ensure_collaborators(payload: dict[str, Any], api: Api, run: Run) -> None:
    org = payload["org"]["name"]
    for repo, wanted in payload["collaborators"].items():
        base = f"/repos/{quote(org)}/{quote(repo)}/collaborators"
        current = {user["login"] for user in api.paged(base)}
        for user, permission in sorted(wanted.items()):
            if user in current:
                level = api.get(f"{base}/{quote(user)}/permission") or {}
                if (level.get("role_name") or level.get("permission")) == permission:
                    continue
            api.call("PUT", f"{base}/{quote(user)}", body={"permission": permission})
            run.note(f"{user} : droit « {permission} » sur {repo}")
        for user in sorted(current - set(wanted)):
            api.call("DELETE", f"{base}/{quote(user)}")
            run.note(f"{user} retiré des collaborateurs de {repo}")


def account_repos(payload: dict[str, Any], user: str) -> list[str]:
    names = {repo for team in payload["teams"] if user in team["members"] for repo in team["repos"]}
    names |= {repo for repo, wanted in payload["collaborators"].items() if user in wanted}
    return sorted(names)


def write_credentials(directory: Path, user: str, password: str, token: str | None) -> None:
    """Writes <directory>/<user>.json, root 0600; without a token (assistant), no "token" key."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(directory, 0, 0)
    os.chmod(directory, 0o700)
    values = {"user": user, "password": password} | ({} if token is None else {"token": token})
    content = json.dumps(values, indent=2) + "\n"
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=f".{user}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        os.replace(temporary, directory / f"{user}.json")
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_credentials(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return stored if isinstance(stored, dict) else None


def new_initial_password(payload: dict[str, Any], api: Api, account: dict[str, Any]) -> str:
    """The account existed but its credential file is gone: a new initial password."""
    password = random_password(payload["password_length"])
    api.call("PATCH", f"/admin/users/{quote(account['user'])}", body={
        "login_name": account["user"], "source_id": 0, "password": password,
        "must_change_password": account["must_change_password"]})
    return password


def ensure_credentials(payload: dict[str, Any], api: Api, run: Run, created: dict[str, str]) -> None:
    """One credential file per account, created when absent.

    A person's file holds a token, limited to the person's repositories and replaced when
    they change: the workstation clones and pushes with it (C-4). An assistant's file holds
    its password only: nothing uses an assistant token (the workstation reads the person's
    file only, ansible/roles/harness/tasks/server.yml), so none is minted and the tokens
    named payload["token"]["name"] left by earlier versions are deleted (D-32).
    """
    org = payload["org"]["name"]
    directory = Path(payload["credentials_dir"])
    spec = payload["token"]
    for account in payload["accounts"]:
        user = account["user"]
        repos = account_repos(payload, user)
        if not repos:
            # A token without repositories would not be limited at all.
            raise Failure(f"le compte {user} n'a accès à aucun dépôt : configuration incohérente")
        path = directory / f"{user}.json"
        stored = read_credentials(path)
        tokens = {token["name"]: token for token in api.paged(f"/users/{quote(user)}/tokens")}
        current = tokens.get(spec["name"])
        if account["kind"] == "assistant":
            if current:
                api.call("DELETE", f"/users/{quote(user)}/tokens/{current['id']}", ok=(204, 404))
                run.note(f"{user} : jeton « {spec['name']} » supprimé (un assistant n'en détient pas)")
            if stored and stored.get("password") and stored.get("user") == user and "token" not in stored:
                continue
            if stored and stored.get("password"):
                password, reason = stored["password"], "fichier d'identifiants réécrit sans jeton"
            elif user in created:
                password, reason = created[user], "identifiants créés (mot de passe, sans jeton)"
            else:
                password = new_initial_password(payload, api, account)
                reason = "nouveau mot de passe initial (fichier d'identifiants absent)"
            write_credentials(directory, user, password, None)
            run.note(f"{user} : {reason}")
            continue
        if stored and stored.get("token") and stored.get("password") and current:
            token_repos = sorted(repo["name"] for repo in current.get("repositories") or [])
            if (current.get("token_last_eight") == stored["token"][-8:] and token_repos == repos
                    and sorted(current.get("scopes") or []) == sorted(spec["scopes"])):
                continue
        if stored and stored.get("password"):
            password = stored["password"]
            reason = "jeton remplacé (dépôts ou droits changés)"
        elif user in created:
            password = created[user]
            reason = "identifiants créés"
        else:
            password = new_initial_password(payload, api, account)
            reason = "nouveau mot de passe initial et nouveau jeton (fichier d'identifiants absent)"
        if current:
            api.call("DELETE", f"/users/{quote(user)}/tokens/{current['id']}", ok=(204, 404))
        _, token = api.call("POST", f"/users/{quote(user)}/tokens", body={
            "name": spec["name"], "scopes": spec["scopes"],
            "repositories": [{"owner": org, "name": name} for name in repos]})
        write_credentials(directory, user, password, token["sha1"])
        run.note(f"{user} : {reason}")


def disable_removed(payload: dict[str, Any], api: Api, run: Run, removed_members: set[str]) -> list[str]:
    """Accounts no longer configured: login prohibited, every token deleted (those the
    person created in the web interface too), password replaced by a random one that is kept
    nowhere, credential file deleted. If the id is declared again later, the account starts
    from a new initial password and a new token: nothing the previous holder kept works."""
    directory = Path(payload["credentials_dir"])
    expected = {account["user"] for account in payload["accounts"]} | {payload["admin"]["user"]}
    files = {path.stem: path for path in directory.glob("*.json")} if directory.is_dir() else {}
    disabled = []
    for user in sorted((set(files) | removed_members) - expected):
        current = api.get(f"/users/{quote(user)}", missing_ok=True) if re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", user) else None
        if current is not None and not current.get("is_admin"):
            if not current.get("prohibit_login"):
                api.call("PATCH", f"/admin/users/{quote(user)}", body={
                    "login_name": user, "source_id": 0, "prohibit_login": True,
                    "password": random_password(payload["password_length"]), "must_change_password": False})
                run.note(f"compte {user} désactivé (retiré de la configuration)")
            for token in api.paged(f"/users/{quote(user)}/tokens"):
                api.call("DELETE", f"/users/{quote(user)}/tokens/{token['id']}", ok=(204, 404))
                run.note(f"compte {user} : jeton « {token.get('name', '?')} » supprimé")
            disabled.append(user)
        if user in files:
            files[user].unlink(missing_ok=True)
            run.note(f"fichier d'identifiants de {user} supprimé")
    return disabled


def step_ensure(payload: dict[str, Any]) -> dict[str, Any]:
    payload = complete(payload)
    api = Api(payload["base_url"], payload["admin"]["user"], payload["admin"]["password"])
    wait_ready(api)
    run = Run()
    ensure_admin(payload, api, run)
    ensure_org(payload, api, run)
    ensure_repos(payload, api, run)
    ensure_protections(payload, api, run)
    created = ensure_accounts(payload, api, run)
    removed = ensure_teams(payload, api, run)
    ensure_collaborators(payload, api, run)
    ensure_credentials(payload, api, run, created)
    disabled = disable_removed(payload, api, run, removed)
    version = (api.get("/version") or {}).get("version", "?")
    return {"changed": bool(run.changes), "changes": run.changes, "version": version,
            "accounts": [account["user"] for account in payload["accounts"]],
            "repos": [repo["name"] for repo in payload["repos"]], "disabled": disabled}


# ----------------------------------------------------------------------------- fill


def step_fill(payload: dict[str, Any]) -> dict[str, Any]:
    """payload["repos"] = {repository: {path: base64 content}}; payload["org"] = organisation."""
    api = Api(payload["base_url"], payload["admin"]["user"], payload["admin"]["password"])
    wait_ready(api)
    org = payload["org"]
    identity = payload["author"]
    filled, skipped = [], []
    for name, files in sorted(payload["repos"].items()):
        path = f"/repos/{quote(org)}/{quote(name)}"
        current = api.get(path, missing_ok=True)
        if current is None:
            raise Failure(f"dépôt {org}/{name} absent : le rôle forgejo doit passer avant le rôle context")
        if not current.get("empty"):
            skipped.append(name)
            continue
        if not files:
            raise Failure(f"aucun fichier à déposer dans {org}/{name}")
        api.call("POST", f"{path}/contents", body={
            "branch": payload["branch"], "message": payload["message"], "author": identity, "committer": identity,
            "files": [{"operation": "create", "path": file_path, "content": content}
                      for file_path, content in sorted(files.items())]})
        if (api.get(path) or {}).get("empty", True):
            raise Failure(f"le dépôt {org}/{name} est encore vide après le premier commit")
        filled.append(name)
    return {"changed": bool(filled), "filled": filled, "skipped": skipped}


# ----------------------------------------------------------------------------- check


def read_app_ini(payload: dict[str, Any]) -> configparser.ConfigParser:
    result = compose(payload, "cat", payload["app_ini"])
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string("[__root__]\n" + result.stdout)
    return parser


def step_check(payload: dict[str, Any]) -> dict[str, Any]:
    payload = complete(payload)
    api = Api(payload["base_url"], payload["admin"]["user"], payload["admin"]["password"])
    anonymous = Api(payload["base_url"])
    org = payload["org"]["name"]
    problems: list[str] = []
    summary: dict[str, Any] = {}

    status, health, _ = api.raw("GET", api.root + "/api/healthz")
    if status != 200 or (health or {}).get("status") != "pass":
        return {"changed": False, "ok": False, "problems": [f"Forgejo ne répond pas sur {api.root}"], "summary": {}}
    status, me = api.call("GET", "/user", ok=(200, 401, 403))
    if status != 200:
        return {"changed": False, "ok": False, "summary": {},
                "problems": [f"connexion de {payload['admin']['user']} refusée ({status}) : relancez l'installation"]}
    summary["version"] = (api.get("/version") or {}).get("version", "?")

    # Settings of C-2, read from the effective app.ini (only booleans leave this script).
    ini = read_app_ini(payload)
    for section, key, wanted, label in [
        ("service", "DISABLE_REGISTRATION", "true", "inscription fermée"),
        ("service", "REQUIRE_SIGNIN_VIEW", "true", "connexion obligatoire pour tout voir"),
        ("server", "OFFLINE_MODE", "true", "mode hors ligne"),
        ("server", "DISABLE_SSH", "true", "SSH de Forgejo désactivé"),
        ("mailer", "ENABLED", "false", "aucun courriel"),
        ("oauth2", "ENABLED", "false", "OAuth désactivé"),
        ("cron.update_checker", "ENABLED", "false", "pas de recherche de mises à jour (télémétrie)"),
        ("mirror", "ENABLED", "false", "miroirs désactivés (aucune copie poussée vers l'extérieur)"),
        ("repository", "FORCE_PRIVATE", "true", "dépôts toujours privés"),
        ("repository", "DISABLE_MIGRATIONS", "true", "pas de migration depuis un autre serveur"),
        ("security", "DISABLE_WEBHOOKS", "true", "webhooks désactivés"),
        ("security", "DISABLE_QUERY_AUTH_TOKEN", "true", "aucun jeton accepté dans l'adresse d'une requête"),
        ("openid", "ENABLE_OPENID_SIGNIN", "false", "OpenID désactivé"),
        ("actions", "ENABLED", "false", "Actions désactivées"),
        ("packages", "ENABLED", "false", "registre de paquets désactivé"),
        ("federation", "ENABLED", "false", "fédération désactivée"),
    ]:
        value = ini.get(section, key, fallback="").strip().lower()
        if value != wanted:
            problems.append(f"réglage {label} absent ([{section}] {key} = {value or 'non défini'})")
    for path, label in [("/api/v1/repos/" + quote(org) + "/entreprise", "l'API"), ("/explore/repos", "l'interface web")]:
        status, _, headers = anonymous.raw("GET", anonymous.root + path)
        if status == 200:
            problems.append(f"{label} de Forgejo répond sans connexion ({path})")
        elif status in (302, 303) and "/user/login" not in headers.get("Location", ""):
            problems.append(f"{label} de Forgejo redirige ailleurs que vers la connexion ({path})")

    current_org = api.get(f"/orgs/{quote(org)}", missing_ok=True)
    if current_org is None:
        problems.append(f"organisation {org} absente")
        return {"changed": False, "ok": False, "problems": problems, "summary": summary}
    if current_org.get("visibility") != "private":
        problems.append(f"organisation {org} non privée")
    repo_names = [repo["name"] for repo in payload["repos"]]
    branch = payload["default_branch"]
    for repo in repo_names:
        current = api.get(f"/repos/{quote(org)}/{quote(repo)}", missing_ok=True)
        if current is None:
            problems.append(f"dépôt {org}/{repo} absent")
            continue
        if not current.get("private"):
            problems.append(f"dépôt {org}/{repo} non privé")
        elif current.get("empty"):
            problems.append(f"dépôt {org}/{repo} vide (contexte non déposé)")
        protection = api.get(f"/repos/{quote(org)}/{quote(repo)}/branch_protections/{quote(branch)}", missing_ok=True)
        if protection is None:
            problems.append(f"branche {branch} de {org}/{repo} non protégée (réécriture et suppression possibles) : "
                            "relancez l'installation")
        elif differences := protection_differences(protection):
            problems.append(f"protection de la branche {branch} de {org}/{repo} modifiée ({', '.join(differences)}) : "
                            "relancez l'installation")
    summary["repos"] = repo_names

    teams = {team["name"]: team for team in api.paged(f"/orgs/{quote(org)}/teams")}
    for spec in payload["teams"]:
        team = teams.get(spec["name"])
        if team is None:
            problems.append(f"équipe {spec['name']} absente")
            continue
        members = sorted(member["login"] for member in api.paged(f"/teams/{team['id']}/members"))
        repos = sorted(repo["name"] for repo in api.paged(f"/teams/{team['id']}/repos"))
        if members != sorted(spec["members"]):
            problems.append(f"équipe {spec['name']} : membres {', '.join(members) or 'aucun'} "
                            f"(attendus : {', '.join(sorted(spec['members']))})")
        if repos != sorted(spec["repos"]):
            problems.append(f"équipe {spec['name']} : dépôts {', '.join(repos) or 'aucun'} "
                            f"(attendus : {', '.join(sorted(spec['repos']))})")
    for repo, wanted in payload["collaborators"].items():
        current = sorted(user["login"] for user in api.paged(f"/repos/{quote(org)}/{quote(repo)}/collaborators"))
        if current != sorted(wanted):
            problems.append(f"{repo} : collaborateurs {', '.join(current) or 'aucun'} (attendus : {', '.join(sorted(wanted))})")

    directory = Path(payload["credentials_dir"])
    if directory.is_dir():
        info = directory.stat()
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != 0:
            problems.append(f"{directory} doit appartenir à root, en 0700")
    for account in payload["accounts"]:
        user = account["user"]
        path = directory / f"{user}.json"
        if not path.exists():
            problems.append(f"fichier d'identifiants absent : {path}")
            continue
        info = path.stat()
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != 0:
            problems.append(f"{path} doit appartenir à root, en 0600")
        stored = read_credentials(path)
        if stored is None:
            problems.append(f"{path} illisible")
            continue
        repos = account_repos(payload, user)
        if account["kind"] == "assistant":
            # An assistant holds no token (D-32): none in its file, none in Forgejo.
            if not stored.get("password") or stored.get("user") != user:
                problems.append(f"{path} incomplet (user, password)")
            if "token" in stored:
                problems.append(f"{path} contient un jeton, alors qu'un assistant n'en détient pas : relancez l'installation")
            held = sorted(token["name"] for token in api.paged(f"/users/{quote(user)}/tokens"))
            if held:
                problems.append(f"{user} détient des jetons ({', '.join(held)}), alors qu'un assistant n'en détient aucun : "
                                f"relancez l'installation (jeton « {payload['token']['name']} ») ou supprimez les autres")
        elif not all(stored.get(key) for key in ("user", "password", "token")) or stored["user"] != user:
            problems.append(f"{path} incomplet (user, password, token)")
            continue
        else:
            status, _ = api.call("GET", f"/repos/{quote(org)}/{quote(repos[0])}", ok=(200, 401, 403, 404),
                                 token=stored["token"])
            if status != 200:
                problems.append(f"jeton de {user} refusé ({status}) : supprimez {path} puis relancez l'installation")
        # Rights of the account itself (the administrator acts as the account): it reads its
        # repositories and none of the others, whatever its token allows.
        for repo in repo_names:
            status, _ = api.call("GET", f"/repos/{quote(org)}/{quote(repo)}", ok=(200, 403, 404), sudo=user)
            if repo in repos and status != 200:
                problems.append(f"{user} ne lit pas {org}/{repo}")
            elif repo not in repos and status == 200:
                problems.append(f"{user} lit {org}/{repo} sans y avoir droit")
    summary["accounts"] = [account["user"] for account in payload["accounts"]]
    return {"changed": False, "ok": not problems, "problems": problems, "summary": summary}


# ----------------------------------------------------------------------------- main

STEPS = {"ensure": step_ensure, "fill": step_fill, "check": step_check}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in STEPS:
        print(json.dumps({"failed": True, "msg": f"usage : forgejo_provision.py {'|'.join(STEPS)} < payload.json"}))
        return 2
    try:
        payload = json.load(sys.stdin)
        result = STEPS[sys.argv[1]](payload)
    except Failure as error:
        print(json.dumps({"failed": True, "msg": str(error)}, ensure_ascii=False))
        return 1
    except (KeyError, TypeError, ValueError) as error:
        print(json.dumps({"failed": True, "msg": f"données d'entrée invalides : {error!r}"}, ensure_ascii=False))
        return 1
    except Exception as error:  # noqa: BLE001 - reported to Ansible as a French message
        print(json.dumps({"failed": True, "msg": f"erreur inattendue : {type(error).__name__} : {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
