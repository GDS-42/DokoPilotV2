#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks a workspace rendered by ansible/playbooks/harness.yml (called by run.sh).

The workspace is the person's clone of <client>/entreprise, with direction/ (dirigeant) and
perso/ cloned inside it. Exits 1 and lists the problems when a check fails. Uses PyYAML
(present wherever Ansible runs) for the Markdown frontmatter.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

SKILLS = ["factures-fournisseurs", "relances-clients", "rapprochement-bancaire", "synthese-activite", "tri-courriels", "devis"]
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DOKOS_DENIED = ["doc_submit", "doc_cancel", "doc_delete"]
MAIL_DENIED = ["send_email", "forward_email", "email_command", "save_to_mailbox", "delete_emails", "download_attachment"]
CALENDAR_DENIED = ["delete_event", "move_event", "respond_to_event"]
HARNESS_FILES = ["opencode.json", ".mcp.json", ".claude/settings.json", ".opencode/agents/bureau.md",
                 ".opencode/plugins/dokopilot-guard.js", ".agents/skills/dokopilot-devis/SKILL.md",
                 ".claude/skills/dokopilot-devis/SKILL.md", "opencode.json.20260101-000000.bak"]

problems: list[str] = []


def check(condition: bool, message: str) -> bool:
    if not condition:
        problems.append(message)
    return condition


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        problems.append(f"{path}: not valid JSON ({error})")
        return None


def frontmatter(path: Path) -> dict[str, Any] | None:
    if not check(path.is_file(), f"{path}: missing"):
        return None
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.DOTALL)
    if not check(match is not None, f"{path}: no YAML frontmatter on the first line"):
        return None
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        problems.append(f"{path}: invalid frontmatter ({error})")
        return None
    check(isinstance(data, dict), f"{path}: frontmatter is not a mapping")
    return data if isinstance(data, dict) else None


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, env=env)


# opencode evaluation, as in packages/core/src/permission.ts (V2) and
# packages/opencode/src/permission/index.ts (V1): last matching rule wins, default ask.
def wildcard(pattern: str, value: str) -> bool:
    def convert(text: str) -> str:
        return "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in text)

    regex = convert(pattern[:-2]) + "(?: .*)?" if pattern.endswith(" *") else convert(pattern)
    return re.fullmatch(regex, value, re.DOTALL) is not None


def flatten_v1(permission: dict[str, Any]) -> list[dict[str, str]]:
    rules = []
    for key, value in permission.items():
        for resource, effect in ({"*": value} if isinstance(value, str) else value).items():
            rules.append({"action": key, "resource": resource, "effect": effect})
    return rules


def effect(rules: list[dict[str, str]], action: str, resource: str) -> str:
    for rule in reversed(rules):
        if wildcard(rule["action"], action) and wildcard(rule["resource"], resource):
            return rule["effect"]
    return "ask"


def check_secrets(args: argparse.Namespace, ws: Path, cfg: Path, secrets: list[str]) -> None:
    token_file = cfg / "dokos-token"
    if args.dokos_url:
        check(token_file.is_file() and mode(token_file) == 0o600, "Dokos token file missing or not 0600")
        check(token_file.read_text(encoding="utf-8") == "fakeapikey0123456789:fakeapisecret9876543210", "Dokos token file content")
    else:
        check(not token_file.exists(), "a Dokos token was stored for a person without Dokos access (D-29)")
    forgejo = json.loads((Path(args.state) / "forgejo" / f"{args.person}.json").read_text(encoding="utf-8"))
    credentials = cfg / "git-credentials"
    # Format written back by git's store helper: the port colon is percent-encoded.
    host = re.sub(r"^https?://", "", args.forgejo_url).rstrip("/").replace(":", "%3a")
    scheme = args.forgejo_url.split(":", 1)[0]
    check(credentials.is_file() and mode(credentials) == 0o600, "git-credentials missing or not 0600")
    check(credentials.read_text(encoding="utf-8") == f"{scheme}://{args.person}:{forgejo['token']}@{host}\n",
          "git-credentials does not hold the person's token for the Forgejo host")
    password = cfg / "forgejo-password"
    check(password.is_file() and mode(password) == 0o600, "forgejo-password missing or not 0600")
    check(password.read_text(encoding="utf-8") == forgejo["password"], "forgejo-password content")
    key = cfg / "model-api-key"
    if args.model_key:
        check(key.is_file() and mode(key) == 0o600, "model-api-key missing or not 0600")
    else:
        check(not key.exists(), "model-api-key written although no model provider needs it")
    check(mode(cfg) == 0o700 and mode(cfg.parent) == 0o700, "private folders are not 0700")
    # No secret in any file of the workspace (clones and .git folders included), nor in the
    # staging copies, nor in what Git recorded.
    for root in (ws, cfg / "rendered"):
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                content = path.read_bytes()
                for secret in secrets:
                    check(secret.encode() not in content, f"{path}: contains a secret")
    for repo in clones(args, ws).values():
        history = git(repo, "log", "-p", "--all").stdout
        for secret in secrets:
            check(secret not in history, f"{repo}: a secret is in the Git history")


def clones(args: argparse.Namespace, ws: Path) -> dict[str, Path]:
    result = {"entreprise": ws, "perso": ws / "perso"}
    if args.dirigeant:
        result["direction"] = ws / "direction"
    return result


def check_clones(args: argparse.Namespace, ws: Path, cfg: Path, forgejo: dict[str, Any]) -> None:
    repos = {"entreprise": "entreprise", "perso": f"perso-{args.person}", "direction": "direction"}
    wanted = clones(args, ws)
    check(args.dirigeant == (ws / "direction").exists(), "direction/ present for a member or missing for the dirigeant")
    for label, path in wanted.items():
        if not check((path / ".git").is_dir(), f"{label}: not a Git clone ({path})"):
            continue
        url = git(path, "config", "--local", "--get", "remote.origin.url").stdout.strip()
        check(url == f"{args.forgejo_url}demo/{repos[label]}.git", f"{label}: remote URL {url!r}")
        check("@" not in url, f"{label}: credentials in the remote URL")
        helpers = git(path, "config", "--local", "--get-all", "credential.helper").stdout.splitlines()
        check(helpers == ["", f"store --file {cfg / 'git-credentials'}"], f"{label}: credential helpers {helpers}")
        check(git(path, "config", "--local", "user.name").stdout.strip() == args.person_name, f"{label}: user.name is not the person")
        check(git(path, "config", "--local", "user.email").stdout.strip() == args.person_email, f"{label}: user.email is not the person")
        config_text = (path / ".git" / "config").read_text(encoding="utf-8")
        check(forgejo["token"] not in config_text and forgejo["password"] not in config_text, f"{label}: secret in .git/config")
        status = git(path, "status", "--porcelain", "--untracked-files=all").stdout
        check(status == "", f"{label}: git status not clean after rendering:\n{status}")
    exclude = (ws / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    check("# BEGIN DokoPilot" in exclude and "/.claude/" in exclude, "entreprise: harness block missing from .git/info/exclude")
    for relative in HARNESS_FILES:
        check(git(ws, "check-ignore", "-q", "--no-index", relative).returncode == 0, f"entreprise: {relative} is not ignored by Git")
    for relative in ("AGENTS.md", "CLAUDE.md"):
        check(git(ws, "ls-files", "--error-unmatch", relative).returncode == 0, f"{relative} does not come from the repository")
    check("@AGENTS.md" in (ws / "CLAUDE.md").read_text(encoding="utf-8"), "CLAUDE.md does not import AGENTS.md")


def check_opencode(args: argparse.Namespace, ws: Path, cfg: Path) -> None:
    version = args.opencode_version
    config = load_json(ws / "opencode.json")
    if not isinstance(config, dict):
        return
    check(config.get("share") == "disabled", "opencode.json: share is not disabled")
    check(config.get("default_agent") == "bureau", "opencode.json: default_agent is not bureau")
    servers = config.get("mcp", {}).get("servers", {}) if version == 2 else config.get("mcp", {})
    expected_servers = ({"dokos"} if args.dokos_url else set()) | ({"mail"} if args.mail else set()) | ({"calendar"} if args.calendar else set())
    check(set(servers) == expected_servers, f"opencode.json: MCP servers {sorted(servers)} instead of {sorted(expected_servers)}")
    if args.dokos_url:
        dokos = servers.get("dokos", {})
        check(dokos.get("url") == args.dokos_url + "/api/method/dokos_ai.mcp.endpoint", f"opencode.json: Dokos URL {dokos.get('url')}")
        check(dokos.get("oauth") is False, "opencode.json: OAuth is not disabled for Dokos")
        check(dokos.get("headers", {}).get("Authorization") == f"token {{file:{cfg / 'dokos-token'}}}",
              "opencode.json: the Dokos header does not read the token file")
    # Models (D-23, D-24).
    key_ref = f"{{file:{cfg / 'model-api-key'}}}"
    check(config.get("model") == (args.model_bureau or None), f"opencode.json: model {config.get('model')!r}")
    if version == 2:
        check(all(s.get("codemode") is False for s in servers.values()), "opencode.json: Code Mode left on")
        agents_cfg = config.get("agents", {})
        check(all(agents_cfg.get(a, {}).get("disabled") is True for a in ("build", "plan", "general", "explore")),
              "opencode.json: built-in agents not disabled")
        check("small_model" not in config, "opencode.json: small_model in a V2 configuration")
        check(agents_cfg.get("title", {}).get("model") == (args.model_small or None),
              f"opencode.json: small model (agents.title.model) {agents_cfg.get('title', {}).get('model')!r}")
        providers = config.get("providers", {})
        check(providers == ({args.provider: {"settings": {"apiKey": key_ref}}} if args.model_key_opencode else {}),
              f"opencode.json: providers {providers}")
        global_rules = config.get("permissions", [])
    else:
        check("permissions" not in config, "opencode.json: V2 permissions in a V1 configuration")
        check(all(config.get("agent", {}).get(a, {}).get("disable") is True for a in ("build", "plan", "general", "explore")),
              "opencode.json: built-in agents not disabled (V1)")
        check(config.get("small_model") == (args.model_small or None), f"opencode.json: small_model {config.get('small_model')!r}")
        provider = config.get("provider", {})
        check(provider == ({args.provider: {"options": {"apiKey": key_ref}}} if args.model_key_opencode else {}),
              f"opencode.json: provider {provider}")
        global_rules = flatten_v1(config.get("permission", {}))
    if args.model_small:
        check(bool(config.get("small_model") or config.get("agents", {}).get("title", {}).get("model")),
              "opencode.json: a model is set but the small model is not")
    if args.calendar:
        env = servers.get("calendar", {}).get("environment", {})
        check(env.get("CALDAV_PASSWORD", "").startswith("{file:"), "opencode.json: CalDAV password not read from its file")

    agents: dict[str, list[dict[str, str]]] = {}
    for name, model in (("bureau", args.model_bureau), ("atelier", args.model_atelier)):
        fm = frontmatter(ws / ".opencode" / "agents" / f"{name}.md")
        if fm is None:
            continue
        check(fm.get("mode") == "primary", f"{name}.md: not a primary agent")
        check(fm.get("model") == (model or None), f"{name}.md: model {fm.get('model')!r} instead of {model!r}")
        own = fm.get("permissions", []) if version == 2 else flatten_v1(fm.get("permission", {}))
        check(len(own) > 0, f"{name}.md: no permission rules")
        agents[name] = global_rules + own

    shell = "shell" if version == 2 else "bash"
    every = list(agents)
    expectations = [
        *[(a, f"dokos_{t}", "*", "deny") for a in every for t in DOKOS_DENIED],
        *[(a, f"mail_{t}", "*", "deny") for a in every for t in MAIL_DENIED],
        *[(a, f"calendar_{t}", "*", "deny") for a in every for t in CALENDAR_DENIED],
        # Context write rules (C-4).
        *[(a, "edit", "00-Boite-de-reception/note.md", "allow") for a in every],
        *[(a, "edit", "perso/memoire-vive.md", "allow") for a in every],
        *[(a, "edit", "perso/AGENTS.md", "ask") for a in every],
        *[(a, "edit", "20-Notes-permanentes/delais.md", "ask") for a in every],
        *[(a, "edit", "direction/rh/note.md", "ask") for a in every],
        *[(a, "edit", "AGENTS.md", "ask") for a in every],
        *[(a, "edit", ".agents/skills/procedure-maison/SKILL.md", "ask") for a in every],
        *[(a, "edit", ".agents/skills/dokopilot-devis/SKILL.md", "deny") for a in every],
        *[(a, "edit", "CLAUDE.md", "deny") for a in every],
        *[(a, "edit", ".git/config", "deny") for a in every],
        *[(a, "edit", "perso/.git/hooks/pre-commit", "deny") for a in every],
        # Credentials (never readable) and Git publication (the person runs bin/dokopilot sync).
        *[(a, "read", f"~/.config/dokopilot/demo/{f}", "deny") for a in every for f in ("git-credentials", "forgejo-password", "model-api-key")],
        *[(a, "edit", "~/.config/dokopilot/demo/git-credentials", "deny") for a in every],
        *[(a, "external_directory", "~/.config/dokopilot/demo/*", "deny") for a in every],
        *[(a, shell, c, "deny") for a in every for c in (
            "cat ~/.config/dokopilot/demo/git-credentials", "git push", "git push origin main", "git -C perso push",
            "cd perso && git push", "git credential fill", "git config credential.helper store",
            "git remote set-url origin http://example.test/x.git", "git -C perso config core.hooksPath x", "opencode debug config")],
        *[(a, "edit", ".opencode/plugins/dokopilot-guard.js", "deny") for a in every],
        *[(a, "edit", "opencode.json", "deny") for a in every],
        # Files that steer the agents cannot be planted in a sub-folder, the free-write ones included.
        *[(a, "edit", f, "deny") for a in every for f in (
            "perso/.claude/skills/x/SKILL.md", "perso/CLAUDE.local.md", "perso/opencode.json", "perso/.mcp.json",
            "00-Boite-de-reception/.opencode/agents/x.md", "00-Boite-de-reception/import/CLAUDE.md", "CLAUDE.local.md",
            "opencode.jsonc", "perso/.agents/skills/x/SKILL.md")],
        *[(a, shell, c, "deny") for a in every for c in ("git replace abc def", "git send-pack origin main")],
        *[(a, "webfetch", "*", "ask") for a in every],
        ("bureau", shell, "ls -la", "ask"),
        ("bureau", shell, "git commit -m 'Ajoute une note'", "ask"),
        ("atelier", shell, "ls -la", "allow"),
        ("atelier", shell, "git commit -m 'Ajoute une note'", "allow"),
    ]
    if args.dokos_url:
        expectations += [(a, "dokos_doc_create", "*", "ask") for a in every] + [(a, "dokos_doc_list", "*", "allow") for a in every]
    else:
        expectations += [(a, "dokos_doc_list", "*", "ask") for a in every]
    if args.mail:
        expectations += [("bureau", "mail_get_emails_content", "*", "allow"), ("bureau", "mail_save_draft", "*", "ask"),
                         ("atelier", "mail_get_emails_content", "*", "deny")]
    if version == 2:
        expectations += [(a, "subagent", "general", "deny") for a in every] + [(a, "execute", "*", "deny") for a in every]
    for agent, action, resource, wanted in expectations:
        got = effect(agents[agent], action, resource)
        check(got == wanted, f"opencode {agent}: {action} {resource!r} gives {got}, expected {wanted}")

    policy = load_json(ws / ".opencode" / "plugins" / "dokopilot-guard.policy.json")
    if isinstance(policy, dict):
        check(policy.get("mail_prefix") == "mail_", "opencode guard policy: mail prefix")
        check(policy.get("git_identity") == {"name": args.assistant_name, "email": args.assistant_email},
              f"opencode guard policy: git identity {policy.get('git_identity')}")
        check("shell" not in policy.get("safe_actions", []) and "edit" not in policy.get("safe_actions", []),
              "opencode guard policy: shell or edit listed as safe")


def check_claude(args: argparse.Namespace, ws: Path, cfg: Path) -> None:
    mcp = load_json(ws / ".mcp.json")
    if isinstance(mcp, dict):
        servers = mcp.get("mcpServers", {})
        expected = ({"dokos"} if args.dokos_url else set()) | ({"mail"} if args.mail else set()) | ({"calendar"} if args.calendar else set())
        check(set(servers) == expected, f".mcp.json: servers {sorted(servers)} instead of {sorted(expected)}")
        if args.dokos_url:
            dokos = servers.get("dokos", {})
            check(dokos.get("type") == "http" and "headers" not in dokos, ".mcp.json: Dokos is not an HTTP server without static headers")
            check(str(cfg / "dokos-token") in dokos.get("headersHelper", ""), ".mcp.json: the Dokos headersHelper does not read the token file")
    settings = load_json(ws / ".claude" / "settings.json")
    if isinstance(settings, dict):
        perms = settings.get("permissions", {})
        deny, ask, allow = perms.get("deny", []), perms.get("ask", []), perms.get("allow", [])
        for tool in DOKOS_DENIED:
            check(f"mcp__dokos__{tool}" in deny, f"settings.json: mcp__dokos__{tool} not denied")
        for tool in MAIL_DENIED:
            check(f"mcp__mail__{tool}" in deny, f"settings.json: mcp__mail__{tool} not denied")
        for rule in ("Read(~/.config/dokopilot/**)", "Edit(~/.config/dokopilot/**)", "Edit(/.claude/**)", "Edit(/.mcp.json)",
                     "Bash(opencode *)", "Bash(git push*)", "Bash(git -C * push*)", "Bash(*git*credential*)", "Bash(git config*)",
                     "Bash(git remote*)", "Bash(*git-credentials*)", "Bash(*model-api-key*)", "Edit(/.git/**)", "Edit(/**/.git/**)",
                     "Edit(/.agents/skills/dokopilot-*/**)", "Edit(/CLAUDE.md)", "Edit(/**/CLAUDE.local.md)", "Edit(/**/.mcp.json)",
                     "Edit(/**/opencode.json)", "Edit(/*/**/.claude/**)", "Edit(/*/**/.opencode/**)", "Edit(/*/**/.agents/**)",
                     "Bash(git replace*)", "Bash(git send-pack*)"):
            check(rule in deny, f"settings.json: {rule} not denied")
        check("Edit(/.agents/**)" not in deny, "settings.json: the company's skills cannot be edited at all")
        for rule in ("Edit(/00-Boite-de-reception/**)", "Edit(/perso/**)"):
            check(rule in allow, f"settings.json: {rule} not allowed")
        check("Edit(/**)" not in allow, "settings.json: every edit allowed in the workspace")
        for rule in ("Edit(/direction/**)", "Edit(/AGENTS.md)", "Edit(/**/AGENTS.md)", "WebFetch", "WebSearch"):
            check(rule in ask, f"settings.json: {rule} not asked")
        if args.dokos_url:
            check("mcp__dokos__doc_list" in allow and "mcp__dokos__doc_create" in ask, "settings.json: Dokos reads or drafts")
        else:
            check(not any(r.startswith("mcp__dokos__") for r in allow + ask), "settings.json: Dokos tools allowed without Dokos access")
        check(not any(r.startswith("mcp__dokos__doc_submit") for r in allow + ask), "settings.json: doc_submit allowed or asked")
        check(perms.get("disableBypassPermissionsMode") == "disable", "settings.json: bypass mode not disabled")
        check(settings.get("agent") == "bureau", "settings.json: the main agent is not bureau")
        identity = {"GIT_AUTHOR_NAME": args.assistant_name, "GIT_AUTHOR_EMAIL": args.assistant_email,
                    "GIT_COMMITTER_NAME": args.assistant_name, "GIT_COMMITTER_EMAIL": args.assistant_email}
        check(settings.get("env") == identity, f"settings.json: env {settings.get('env')} instead of the assistant's identity")
        helper = settings.get("apiKeyHelper")
        if args.claude_auth == "api_key":
            check(isinstance(helper, str) and "model_key_helper.py" in helper and str(cfg / "model-api-key") in helper,
                  f"settings.json: apiKeyHelper {helper!r}")
        else:
            check(helper is None, "settings.json: apiKeyHelper set with a subscription")
        enabled = (["dokos"] if args.dokos_url else []) + (["mail"] if args.mail else []) + (["calendar"] if args.calendar else [])
        check(settings.get("enabledMcpjsonServers") == enabled, f"settings.json: enabledMcpjsonServers {settings.get('enabledMcpjsonServers')}")
        hooks = settings.get("hooks", {})
        check(any(g.get("matcher") == "*" and any(h.get("onFailure") == "block" for h in g.get("hooks", []))
                  for g in hooks.get("PreToolUse", [])), "settings.json: no fail-closed PreToolUse guard on every tool")
        sandbox = settings.get("sandbox", {})
        check(sandbox.get("enabled") is True and sandbox.get("allowUnsandboxedCommands") is False, "settings.json: sandbox not strict")
        fs = sandbox.get("filesystem", {})
        check(str(cfg.parent) in fs.get("denyRead", []), "settings.json: sandbox can read the credentials")
        for path in (str(cfg.parent), "./.git/config", "./.git/hooks", "./perso/.git/config", "./AGENTS.md", "./.agents/skills/dokopilot-devis"):
            check(path in fs.get("denyWrite", []), f"settings.json: sandbox can write {path}")
        check(sandbox.get("network", {}).get("allowedDomains") == ([re.sub(r"^https?://", "", args.dokos_url)] if args.dokos_url else []),
              "settings.json: sandbox network")
    bureau = frontmatter(ws / ".claude" / "agents" / "bureau.md")
    atelier = frontmatter(ws / ".claude" / "agents" / "atelier.md")
    check(bureau is not None and bureau.get("name") == "bureau", ".claude/agents/bureau.md: wrong name")
    check(atelier is not None and "mcp__mail__*" in atelier.get("disallowedTools", []), "atelier: mail tools not removed")
    guard = load_json(ws / ".claude" / "hooks" / "guard-policy.json")
    if isinstance(guard, dict):
        check(guard.get("free_write_dirs") == [str(ws / "00-Boite-de-reception"), str(ws / "perso")], "Claude guard policy: free-write folders")
        check("Bash" not in guard.get("safe_tools", []) and "Edit" not in guard.get("safe_tools", []), "Claude guard policy: Bash or Edit listed as safe")


def check_skills(ws: Path, folder: str) -> None:
    for skill in SKILLS:
        check(not (ws / folder / skill).exists(), f"{folder}/{skill}: skill installed without the dokopilot- prefix")
        path = ws / folder / f"dokopilot-{skill}" / "SKILL.md"
        fm = frontmatter(path)
        if fm is None:
            continue
        name, description = fm.get("name"), fm.get("description")
        check(name == f"dokopilot-{skill}", f"{path}: name {name!r} does not match its folder")
        check(isinstance(name, str) and bool(SKILL_NAME.match(name)) and len(name) <= 64, f"{path}: invalid name")
        check(isinstance(description, str) and 0 < len(description.strip()) <= 1024, f"{path}: invalid description")
        check("Ce que vous ne faites jamais" in path.read_text(encoding="utf-8"), f"{path}: no « Ce que vous ne faites jamais » section")


def check_import(args: argparse.Namespace, ws: Path, cfg: Path) -> None:
    folder = ws / "00-Boite-de-reception" / f"import-{args.import_date}"
    if not check(folder.is_dir(), f"import folder {folder} missing"):
        return
    check((folder / "notes" / "fournisseurs.md").is_file() and (folder / "accueil.md").is_file(), "import: notes not copied")
    check(not (folder / ".obsidian").exists() and not (folder / "lien-ssh").exists(), "import: .obsidian or a symbolic link copied")
    check(all(not p.is_symlink() for p in folder.rglob("*")), "import: symbolic link copied")
    check(all(mode(p) & 0o111 == 0 for p in folder.rglob("*") if p.is_file()), "import: executable file copied")
    relative = str(folder.relative_to(ws))
    log = git(ws, "log", "--format=%an <%ae>|%cn <%ce>", "--", relative).stdout.splitlines()
    person = f"{args.person_name} <{args.person_email}>"
    check(log == [f"{person}|{person}"], f"import: commits {log} instead of one commit by the person")
    check(git(ws, "rev-list", "--count", "@{u}..HEAD").stdout.strip() == "1", "import: commit missing or pushed")
    marker = cfg / "imports.json"
    check(marker.is_file() and mode(marker) == 0o600, "import marker missing or not 0600")
    imports = json.loads(marker.read_text(encoding="utf-8")).get("imports", []) if marker.is_file() else []
    check(len(imports) == 1 and imports[0].get("files") == 2, f"import marker {imports}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--state", required=True, help="fake server state (credential files) of fake_forgejo.py")
    parser.add_argument("--secret", action="append", default=[], help="a value that must appear nowhere in the workspace")
    parser.add_argument("--person", required=True)
    parser.add_argument("--person-name", required=True)
    parser.add_argument("--person-email", required=True)
    parser.add_argument("--assistant-name", required=True)
    parser.add_argument("--assistant-email", required=True)
    parser.add_argument("--dirigeant", action="store_true")
    parser.add_argument("--forgejo-url", required=True)
    parser.add_argument("--dokos-url", default="", help="empty: no Dokos access (member, D-29)")
    parser.add_argument("--harness", action="append", default=[], choices=["opencode", "claude-code"])
    parser.add_argument("--opencode-version", type=int, default=2)
    parser.add_argument("--mail", action="store_true")
    parser.add_argument("--calendar", action="store_true")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model-bureau", default="")
    parser.add_argument("--model-atelier", default="")
    parser.add_argument("--model-small", default="")
    parser.add_argument("--model-key", action="store_true", help="the model key file is expected")
    parser.add_argument("--model-key-opencode", action="store_true", help="opencode reads the model key file")
    parser.add_argument("--claude-auth", default="subscription")
    parser.add_argument("--import-date", default="")
    args = parser.parse_args(argv)

    ws, cfg = args.workspace, args.config_dir
    forgejo = json.loads((Path(args.state) / "forgejo" / f"{args.person}.json").read_text(encoding="utf-8"))
    secrets = args.secret + [forgejo["token"], forgejo["password"]]
    check_clones(args, ws, cfg, forgejo)
    check_secrets(args, ws, cfg, secrets)
    for path in [*ws.rglob("*.json")]:
        if path.is_file() and ".git" not in path.parts:
            load_json(path)
    if "opencode" in args.harness:
        check_opencode(args, ws, cfg)
        check_skills(ws, ".agents/skills")
    else:
        check(not (ws / "opencode.json").exists() and not (ws / ".opencode").exists(), "opencode files rendered while disabled")
    if "claude-code" in args.harness:
        check_claude(args, ws, cfg)
        check_skills(ws, ".claude/skills")
    else:
        check(not (ws / ".mcp.json").exists() and not (ws / ".claude").exists(), "Claude Code files rendered while disabled")
    if args.import_date:
        check_import(args, ws, cfg)
    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
