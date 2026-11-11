#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Context clones of the workstation: clone, update, configure; import a folder into the inbox.

Called by ansible/roles/harness/tasks/context.yml and import.yml with a JSON payload on stdin.
Prints one JSON object on stdout: {"changed": bool, "failed": bool, "msg": "...", ...}; the
messages are in French (they are shown to the person). Exit status 1 when failed.

The payload never holds a secret. The clones authenticate with git's `store` credential
helper, which reads the person's token from the private file named in the payload: the token
never goes into a remote URL, a clone's configuration or a command line.

Every git call runs with the GIT_* environment variables removed, GIT_TERMINAL_PROMPT=0 (git
never waits for a password), GIT_NO_REPLACE_OBJECTS=1 (a replacement object cannot show one
commit and send another), core.hooksPath=/dev/null and core.fsmonitor=false (nothing planted
in a clone runs), and, for network calls, the credential helper list reset to the store helper
alone (no keychain of the workstation receives the token). Before a clone is touched, it is
audited: a local configuration key outside an allowlist (it could run a program or send the
traffic elsewhere), replacement objects or grafts, a nested repository or a submodule (their
own configuration and hooks would run at the person's next plain `git status`) stop the run:
an agent could have written them.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

BLOCK_BEGIN = "# BEGIN DokoPilot (bin/dokopilot harness) : fichiers propres à ce poste"
BLOCK_END = "# END DokoPilot"

# Local configuration keys a clone may hold. Anything else stops the run.
ALLOWED_KEYS = {
    "core.repositoryformatversion", "core.filemode", "core.bare", "core.logallrefupdates",
    "core.ignorecase", "core.precomposeunicode", "core.symlinks", "core.autocrlf", "core.eol",
    "core.safecrlf", "core.quotepath", "user.name", "user.email", "credential.helper",
    "pull.rebase", "pull.ff", "push.default", "push.autosetupremote", "fetch.prune", "merge.ff",
    "rebase.autostash", "init.defaultbranch", "remote.origin.url", "remote.origin.fetch",
}
ALLOWED_PATTERNS = (re.compile(r"^branch\..+\.(remote|merge|rebase)$"), re.compile(r"^color\.[a-z.]+$"))
# Files that steer the agents, as Git pathspecs: a change received from the server is never
# taken silently (bin/dokopilot sync reads this list, shows the change and asks). They match
# at any depth: Claude Code and opencode also load the AGENTS.md, CLAUDE.md, CLAUDE.local.md
# and .claude/ of a sub-folder once a file of that folder is read
# (https://code.claude.com/docs/en/memory, https://opencode.ai/docs/rules/).
RULE_PATHS = tuple(f":(glob){pattern}" for pattern in (
    "**/AGENTS.md", "**/CLAUDE.md", "**/CLAUDE.local.md", "**/.agents/**", "**/.claude/**", "**/.opencode/**",
    "**/opencode.json", "**/opencode.jsonc", "**/.mcp.json", "**/.gitignore", "**/.gitattributes", ".gitmodules",
))
# Clones that live inside another clone of the workspace (ignored by it, audited on their own).
NESTED_CLONES = {"entreprise": ("direction", "perso")}


class Failure(Exception):
    """A problem explained to the person, in French."""


class Run:
    """Accumulates what changed, so that a failure still reports earlier changes."""

    def __init__(self) -> None:
        self.changed = False


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="", GIT_NO_REPLACE_OBJECTS="1", LC_ALL="C")
    env.update(extra or {})
    return env


def git(path: Path | None, *args: str, credential_file: str = "", env: dict[str, str] | None = None,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.quotePath=true"]
    if credential_file:
        cmd += ["-c", "credential.helper=", "-c", "credential.helper=" + helper(credential_file)]
    if path is not None:
        cmd += ["-C", str(path)]
    cmd += list(args)
    result = subprocess.run(cmd, text=True, capture_output=True, env=clean_env(env), stdin=subprocess.DEVNULL)
    if check and result.returncode != 0:
        raise Failure(f"git {args[0]} a échoué dans {path or '.'} :\n{tail(result)}")
    return result


def tail(result: subprocess.CompletedProcess[str]) -> str:
    lines = (result.stderr or result.stdout).strip().splitlines()
    return "\n".join(f"  {line}" for line in lines[-6:])


def helper(credential_file: str) -> str:
    return "store --file " + shlex.quote(credential_file)


def local_config(path: Path) -> list[tuple[str, str]]:
    """Local configuration of a clone (read only: no include is followed, nothing runs)."""
    result = git(path, "config", "--local", "--list", "--null", check=False)
    if result.returncode not in (0, 1):
        raise Failure(f"Configuration Git illisible dans {path} :\n{tail(result)}")
    entries = []
    for item in result.stdout.split("\0"):
        if item:
            key, _, value = item.partition("\n")
            entries.append((key, value))
    return entries


def nested_repositories(path: Path, label: str) -> list[str]:
    """Git repositories inside a clone, other than the workspace's own nested clones. Git
    runs the configuration and the hooks of such a repository (fsmonitor, for instance) as
    soon as someone runs a plain `git status` above it."""
    skip = {path / ".git", *(path / name for name in NESTED_CLONES.get(label, ()))}
    found = []
    for root, dirs, files in os.walk(path, followlinks=False):
        here = Path(root)
        for name in [*dirs, *files]:
            if name == ".git" and here != path:
                found.append(str(here.relative_to(path)))
        dirs[:] = [name for name in dirs if name != ".git" and here / name not in skip]
    return sorted(found)


def audit_content(path: Path, label: str) -> None:
    """Refuses what could show the person one thing and send another, or run a program at
    their next git command: replacement objects, grafts, submodules, nested repositories."""
    problems = []
    if git(path, "for-each-ref", "--count=1", "refs/replace/", check=False).stdout.strip():
        problems.append("des objets de remplacement (git replace)")
    if (path / ".git" / "info" / "grafts").exists():
        problems.append("des greffes d'historique (.git/info/grafts)")
    staged = git(path, "ls-files", "--stage", "-z", check=False).stdout.split("\0")
    links = sorted(entry.partition("\t")[2] for entry in staged if entry.startswith("160000 "))
    if links or (path / ".gitmodules").exists():
        problems.append("un sous-module ou un dépôt Git enregistré comme tel (" + ", ".join(links[:5] or [".gitmodules"]) + ")")
    nested = nested_repositories(path, label)
    if nested:
        problems.append("un dépôt Git dans un sous-dossier (" + ", ".join(nested[:5]) + ")")
    if problems:
        raise Failure(
            f"La copie « {label} » ({path}) contient {' ; '.join(problems)}.\n"
            "Un agent a pu les créer : ils peuvent montrer un contenu et en envoyer un autre, ou lancer un programme "
            "à votre prochaine commande git. Ne lancez pas git dans ce dossier ; retirez-les (supprimez le sous-dossier "
            f"ou son dossier .git ; « git -C {path} replace -d <objet> » pour un objet de remplacement), "
            "puis relancez « bin/dokopilot harness »."
        )


def audit(path: Path, label: str) -> None:
    """Refuses a clone whose configuration could run a program or redirect its traffic, or
    whose content hides what would be sent (audit_content)."""
    entries = local_config(path)
    unexpected = sorted({key for key, _ in entries
                         if key.lower() not in ALLOWED_KEYS and not any(p.match(key.lower()) for p in ALLOWED_PATTERNS)})
    # A branch that follows another remote, or a .git that points elsewhere (commondir).
    unexpected += sorted({f"{key}={value}" for key, value in entries if key.lower().endswith(".remote") and value != "origin"})
    common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False).stdout.strip()
    if common and Path(common).resolve() != (path / ".git").resolve():
        unexpected.append(f"dossier Git commun {common}")
    if unexpected:
        raise Failure(
            f"La copie « {label} » ({path}) a des réglages Git inattendus : {', '.join(unexpected)}.\n"
            "Ils pourraient lancer un programme ou envoyer vos identifiants ailleurs (un agent a pu les écrire). "
            f"Vérifiez {path}/.git/config, retirez ces lignes (git -C {path} config --local --unset <réglage>), "
            "puis relancez « bin/dokopilot harness »."
        )
    audit_content(path, label)


def config_values(path: Path, key: str) -> list[str]:
    return [value for name, value in local_config(path) if name.lower() == key]


def configure(path: Path, credential_file: str, identity: dict[str, str], run: Run) -> None:
    """credential.helper = ["", store --file <file>] (local, resets the global helpers); user.*."""
    wanted = ["", helper(credential_file)]
    if config_values(path, "credential.helper") != wanted:
        git(path, "config", "--local", "--unset-all", "credential.helper", check=False)
        for value in wanted:
            git(path, "config", "--local", "--add", "credential.helper", value)
        run.changed = True
    for key in ("name", "email"):
        if config_values(path, f"user.{key}") != [identity[key]]:
            git(path, "config", "--local", "--replace-all", f"user.{key}", identity[key])
            run.changed = True


def repo_suffix(url: str) -> str:
    match = re.search(r"/([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else url


def check_remote(path: Path, label: str, url: str, run: Run) -> None:
    current = config_values(path, "remote.origin.url")
    if current == [url]:
        return
    if len(current) == 1 and repo_suffix(current[0]) == repo_suffix(url):
        # Same repository, new address of the server (address or MagicDNS name changed).
        git(path, "remote", "set-url", "origin", url)
        run.changed = True
        return
    raise Failure(
        f"La copie « {label} » ({path}) ne vient pas du dépôt attendu ({repo_suffix(url)}) : "
        f"origine {', '.join(current) or 'absente'}.\nDéplacez ce dossier ou indiquez un autre dossier "
        "de travail (--dest), puis relancez « bin/dokopilot harness »."
    )


def write_exclude(path: Path, lines: list[str], run: Run) -> None:
    exclude = path / ".git" / "info" / "exclude"
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    block = "\n".join([BLOCK_BEGIN, *lines, BLOCK_END]) + "\n"
    pattern = re.compile(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.DOTALL)
    if pattern.search(text):
        new = pattern.sub(lambda _: block, text, count=1)
    else:
        new = text + ("" if not text or text.endswith("\n") else "\n") + block
    if new != text:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(new, encoding="utf-8")
        run.changed = True


def head(path: Path) -> str:
    result = git(path, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def update(path: Path, label: str, credential_file: str, run: Run) -> dict[str, Any]:
    """git pull --ff-only, when the clone is clean; reports instead of failing otherwise."""
    before = head(path)
    fetch = git(path, "fetch", "--quiet", "origin", credential_file=credential_file, check=False)
    if fetch.returncode != 0:
        return {"state": "unreachable", "message": f"{label} : serveur injoignable, copie laissée telle quelle.\n{tail(fetch)}"}
    if not before:
        branch = git(path, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD", check=False).stdout.strip()
        branch = branch.removeprefix("origin/") or "main"
        if git(path, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}", check=False).returncode != 0:
            return {"state": "empty", "message": f"{label} : le dépôt est encore vide sur le serveur."}
        git(path, "checkout", "-q", "-B", branch, "--track", f"origin/{branch}")
        run.changed = True
        return {"state": "updated", "message": f"{label} : contenu reçu du serveur."}
    if git(path, "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        return {"state": "kept", "message": (f"{label} : modifications locales non enregistrées, copie non mise à jour "
                                             "(enregistrez-les, puis lancez « bin/dokopilot sync »).")}
    if git(path, "rev-parse", "--verify", "-q", "@{u}", check=False).returncode != 0:
        return {"state": "kept", "message": f"{label} : branche sans suivi du serveur, copie non mise à jour."}
    upstream = git(path, "rev-parse", "@{u}").stdout.strip()
    if upstream == before:
        return {"state": "current", "message": f"{label} : à jour."}
    if git(path, "merge-base", "--is-ancestor", "HEAD", "@{u}", check=False).returncode == 0:
        rules = git(path, "diff", "--name-only", "HEAD", "@{u}", "--", *RULE_PATHS).stdout.split()
        if rules:
            return {"state": "rules", "message": (f"{label} : les règles des agents ont changé sur le serveur ({', '.join(rules[:5])}) ; "
                                                  "copie non mise à jour : lancez « bin/dokopilot sync », qui les montre et "
                                                  "vous demande de les accepter.")}
        git(path, "merge", "--ff-only", "--quiet", "@{u}")
        run.changed = True
        return {"state": "updated", "message": f"{label} : mis à jour depuis le serveur."}
    if git(path, "merge-base", "--is-ancestor", "@{u}", "HEAD", check=False).returncode == 0:
        return {"state": "ahead", "message": f"{label} : des changements de ce poste attendent « bin/dokopilot sync »."}
    return {"state": "diverged", "message": (f"{label} : vos changements et ceux du serveur ont divergé ; "
                                             "lancez « bin/dokopilot sync » pour les réunir.")}


def clones(payload: dict[str, Any], run: Run) -> dict[str, Any]:
    credential_file = payload["credential_file"]
    identity = payload["identity"]
    reports = []
    for repo in payload["repos"]:
        path, label, url = Path(repo["path"]), repo["label"], repo["url"]
        if not (path / ".git").is_dir():
            if path.exists() and (not path.is_dir() or any(path.iterdir())):
                raise Failure(
                    f"Le dossier {path} existe déjà et n'est pas une copie Git du dépôt « {label} » (ancien dossier "
                    f"de travail de la version 0.1 ?).\nDéplacez-le, par exemple « mv {path} {path}.ancien », puis "
                    "relancez « bin/dokopilot harness » : la configuration du harnais sera recréée dans la copie."
                )
            result = git(None, "clone", "--quiet", "--origin", "origin", "--", url, str(path),
                         credential_file=credential_file, check=False)
            if result.returncode != 0:
                raise Failure(f"Impossible de copier le dépôt « {label} » depuis Forgejo ({url}) :\n{tail(result)}")
            run.changed = True
            audit(path, label)
            configure(path, credential_file, identity, run)
            write_exclude(path, repo.get("exclude", []), run)
            report = {"state": "cloned", "message": f"{label} : copié depuis le serveur."}
            if not head(path):
                report = {"state": "empty", "message": f"{label} : copié, mais le dépôt est encore vide sur le serveur."}
        else:
            audit(path, label)
            check_remote(path, label, url, run)
            configure(path, credential_file, identity, run)
            write_exclude(path, repo.get("exclude", []), run)
            report = update(path, label, credential_file, run)
        report.update(label=label, path=str(path), head=head(path))
        reports.append(report)
    return {"repos": reports}


def inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def import_folder(payload: dict[str, Any], run: Run) -> dict[str, Any]:
    repo = Path(payload["repo"])
    source = Path(payload["source"]).expanduser()
    marker = Path(payload["marker"])
    done = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {"imports": []}
    if not source.is_dir():
        raise Failure(f"Le dossier à importer (context.import_path) {source} n'existe pas ou n'est pas un dossier.")
    resolved = source.resolve()
    for previous in done.get("imports", []):
        if previous.get("source") == str(resolved):
            return {"imported": False, "folder": previous.get("folder", ""),
                    "message": f"Import déjà fait le {previous.get('date', '?')} ({previous.get('folder', '?')})."}
    for protected in [repo, *map(Path, payload.get("protected", []))]:
        protected = protected.resolve()
        if inside(resolved, protected) or inside(protected, resolved):
            raise Failure(f"Le dossier à importer {resolved} recouvre {protected} : choisissez un dossier distinct "
                          "(context.import_path), hors du dossier de travail et de ~/.config/dokopilot.")
    inbox = repo / payload["inbox"]
    name = f"import-{payload['date']}"
    index = 2
    while (inbox / name).exists():
        name = f"import-{payload['date']}-{index}"
        index += 1
    target = inbox / name
    skip = set(payload.get("skip_names", []))
    copied, skipped = 0, []
    for root, dirs, files in os.walk(resolved, followlinks=False):
        here = Path(root)
        for directory in list(dirs):
            if directory in skip or (here / directory).is_symlink():
                skipped.append(str((here / directory).relative_to(resolved)) + "/")
                dirs.remove(directory)
        for file in files:
            path = here / file
            relative = path.relative_to(resolved)
            if file in skip or path.is_symlink() or not path.is_file():
                skipped.append(str(relative))
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            # Imported files are data: never executable.
            destination.chmod(0o644)
            copied += 1
    commit = ""
    if copied:
        identity = payload["identity"]
        env = {"GIT_AUTHOR_NAME": identity["name"], "GIT_AUTHOR_EMAIL": identity["email"],
               "GIT_COMMITTER_NAME": identity["name"], "GIT_COMMITTER_EMAIL": identity["email"]}
        relative_target = str(target.relative_to(repo))
        git(repo, "add", "--", relative_target, env=env)
        staged = git(repo, "diff", "--cached", "--name-only", "--", relative_target, env=env).stdout.strip()
        if staged:
            message = f"Importe le dossier « {resolved.name} » dans la boîte de réception (à trier)"
            git(repo, "commit", "--quiet", "--no-verify", "-m", message, "--", relative_target, env=env)
            commit = head(repo)
    done.setdefault("imports", []).append({"source": str(resolved), "date": payload["date"], "folder": name,
                                           "files": copied, "commit": commit})
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=marker.parent, prefix=".imports-")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(done, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, marker)
    run.changed = True
    return {"imported": True, "folder": name, "files": copied, "skipped": skipped[:50], "commit": commit,
            "message": (f"{copied} fichier(s) importé(s) dans {payload['inbox']}/{name}, enregistrés dans Git sur ce "
                        "poste seulement : relisez-les, puis envoyez-les avec « bin/dokopilot sync »."
                        if copied else f"Le dossier {resolved} ne contient aucun fichier à importer.")}


ACTIONS = {"clones": clones, "import": import_folder}


def main() -> int:
    run = Run()
    try:
        payload = json.loads(sys.stdin.read())
        action = ACTIONS[payload["action"]]
        result = action(payload, run)
        result.update(changed=run.changed, failed=False)
    except Failure as error:
        result = {"changed": run.changed, "failed": True, "msg": str(error)}
    except (ValueError, KeyError, TypeError, OSError) as error:
        result = {"changed": run.changed, "failed": True, "msg": f"Erreur interne de la préparation des copies Git : {error!r}"}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
