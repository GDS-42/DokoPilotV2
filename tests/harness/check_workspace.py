#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks a workspace rendered by ansible/playbooks/harness.yml (called by run.sh).

Exits 1 and lists the problems when a check fails. Uses PyYAML (present wherever Ansible
runs) for the fake vars file and the Markdown frontmatter.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

import yaml

SKILLS = ["factures-fournisseurs", "relances-clients", "rapprochement-bancaire", "synthese-activite", "tri-courriels", "devis"]
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DOKOS_DENIED = ["doc_submit", "doc_cancel", "doc_delete"]
MAIL_DENIED = ["send_email", "forward_email", "email_command", "save_to_mailbox", "delete_emails", "download_attachment"]
CALENDAR_DENIED = ["delete_event", "move_event", "respond_to_event"]
V1_NAMES = {"shell": "bash", "subagent": "task"}

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


def check_opencode(ws: Path, version: int, mail: bool, calendar: bool, token_file: str, dokos_url: str) -> None:
    config = load_json(ws / "opencode.json")
    if not isinstance(config, dict):
        return
    check(config.get("share") == "disabled", "opencode.json: share is not disabled")
    check(config.get("default_agent") == "bureau", "opencode.json: default_agent is not bureau")
    servers = config.get("mcp", {}).get("servers", {}) if version == 2 else config.get("mcp", {})
    expected_servers = {"dokos"} | ({"mail"} if mail else set()) | ({"calendar"} if calendar else set())
    check(set(servers) == expected_servers, f"opencode.json: MCP servers {sorted(servers)} instead of {sorted(expected_servers)}")
    dokos = servers.get("dokos", {})
    check(dokos.get("url") == dokos_url + "/api/method/dokos_ai.mcp.endpoint", f"opencode.json: Dokos URL {dokos.get('url')}")
    check(dokos.get("oauth") is False, "opencode.json: OAuth is not disabled for Dokos")
    check(dokos.get("headers", {}).get("Authorization") == f"token {{file:{token_file}}}",
          "opencode.json: the Dokos header does not read the token file")
    if version == 2:
        check(all(s.get("codemode") is False for s in servers.values()), "opencode.json: Code Mode left on")
        disabled = config.get("agents", {})
        check(all(disabled.get(a, {}).get("disabled") is True for a in ("build", "plan", "general", "explore")),
              "opencode.json: built-in agents not disabled")
        global_rules = config.get("permissions", [])
    else:
        check("permissions" not in config, "opencode.json: V2 permissions in a V1 configuration")
        check(all(config.get("agent", {}).get(a, {}).get("disable") is True for a in ("build", "plan", "general", "explore")),
              "opencode.json: built-in agents not disabled (V1)")
        global_rules = flatten_v1(config.get("permission", {}))
    if calendar:
        env = servers.get("calendar", {}).get("environment", {})
        check(env.get("CALDAV_PASSWORD", "").startswith("{file:"), "opencode.json: CalDAV password not read from its file")
        check("delete_event" in env.get("CALDAV_DENY_TOOLS", ""), "opencode.json: CalDAV deletions not removed on the server")

    agents: dict[str, list[dict[str, str]]] = {}
    for name in ("bureau", "atelier"):
        fm = frontmatter(ws / ".opencode" / "agents" / f"{name}.md")
        if fm is None:
            continue
        check(fm.get("mode") == "primary", f"{name}.md: not a primary agent")
        own = fm.get("permissions", []) if version == 2 else flatten_v1(fm.get("permission", {}))
        check(len(own) > 0, f"{name}.md: no permission rules")
        agents[name] = global_rules + own

    shell = "shell" if version == 2 else "bash"
    expectations = [
        # (agent, action, resource, expected effect)
        *[(a, f"dokos_{t}", "*", "deny") for a in agents for t in DOKOS_DENIED],
        *[(a, f"mail_{t}", "*", "deny") for a in agents for t in MAIL_DENIED],
        *[(a, f"calendar_{t}", "*", "deny") for a in agents for t in CALENDAR_DENIED],
        *[(a, "dokos_doc_create", "*", "ask") for a in agents],
        *[(a, "dokos_doc_list", "*", "allow") for a in agents],
        *[(a, "read", "~/.config/dokopilot/demo/dokos-token", "deny") for a in agents],
        *[(a, "external_directory", "~/.config/dokopilot/demo/*", "deny") for a in agents],
        *[(a, shell, "cat ~/.config/dokopilot/demo/dokos-token", "deny") for a in agents],
        *[(a, shell, "opencode debug config", "deny") for a in agents],
        *[(a, "edit", ".opencode/plugins/dokopilot-guard.js", "deny") for a in agents],
        *[(a, "edit", "opencode.json", "deny") for a in agents],
        *[(a, "webfetch", "*", "ask") for a in agents],
        *[(a, "external_directory", "/etc/*", "ask") for a in agents],
        ("bureau", shell, "ls -la", "ask"),
        ("bureau", "edit", "notes.md", "ask"),
        ("atelier", shell, "ls -la", "allow"),
        ("atelier", "edit", "notes.md", "allow"),
        ("atelier", "edit", "AGENTS.md", "ask"),
    ]
    if mail:
        expectations += [("bureau", "mail_get_emails_content", "*", "allow"), ("bureau", "mail_save_draft", "*", "ask"),
                         ("atelier", "mail_get_emails_content", "*", "deny")]
    if version == 2:
        expectations += [(a, "subagent", "general", "deny") for a in agents] + [(a, "execute", "*", "deny") for a in agents]
    for agent, action, resource, wanted in expectations:
        got = effect(agents[agent], action, resource)
        check(got == wanted, f"opencode {agent}: {action} {resource!r} gives {got}, expected {wanted}")

    check((ws / ".opencode" / "plugins" / "dokopilot-guard.js").is_file(), "opencode guard plugin missing")
    policy = load_json(ws / ".opencode" / "plugins" / "dokopilot-guard.policy.json")
    if isinstance(policy, dict):
        check(policy.get("mail_prefix") == "mail_", "opencode guard policy: mail prefix")
        check("mail_get_emails_content" in policy.get("safe_actions", []), "opencode guard policy: mail reads not listed")
        check("dokos_doc_create" in policy.get("always_ask", []), "opencode guard policy: Dokos writes not always asked")
        check("shell" not in policy.get("safe_actions", []) and "edit" not in policy.get("safe_actions", []),
              "opencode guard policy: shell or edit listed as safe")


def check_claude(ws: Path, mail: bool, calendar: bool, token_file: str, config_root: str, dokos_host: str) -> None:
    check("@AGENTS.md" in (ws / "CLAUDE.md").read_text(encoding="utf-8"), "CLAUDE.md does not import AGENTS.md")
    mcp = load_json(ws / ".mcp.json")
    if isinstance(mcp, dict):
        servers = mcp.get("mcpServers", {})
        expected = {"dokos"} | ({"mail"} if mail else set()) | ({"calendar"} if calendar else set())
        check(set(servers) == expected, f".mcp.json: servers {sorted(servers)} instead of {sorted(expected)}")
        dokos = servers.get("dokos", {})
        check(dokos.get("type") == "http", ".mcp.json: Dokos is not an HTTP server")
        check("headers" not in dokos, ".mcp.json: static headers for Dokos")
        check(token_file in dokos.get("headersHelper", "") and "dokos_headers.py" in dokos.get("headersHelper", ""),
              ".mcp.json: the Dokos headersHelper does not read the token file")
        if calendar:
            cal = servers.get("calendar", {})
            check("CALDAV_PASSWORD" not in cal.get("env", {}), ".mcp.json: CalDAV password in the environment")
            check(any("caldav_launcher.py" in a for a in cal.get("args", [])), ".mcp.json: CalDAV not started by the launcher")
    settings = load_json(ws / ".claude" / "settings.json")
    if isinstance(settings, dict):
        perms = settings.get("permissions", {})
        deny, ask, allow = perms.get("deny", []), perms.get("ask", []), perms.get("allow", [])
        for tool in DOKOS_DENIED:
            check(f"mcp__dokos__{tool}" in deny, f"settings.json: mcp__dokos__{tool} not denied")
        for tool in MAIL_DENIED:
            check(f"mcp__mail__{tool}" in deny, f"settings.json: mcp__mail__{tool} not denied")
        for tool in CALENDAR_DENIED:
            check(f"mcp__calendar__{tool}" in deny, f"settings.json: mcp__calendar__{tool} not denied")
        for rule in ("Read(~/.config/dokopilot/**)", "Edit(/.claude/**)", "Edit(/.mcp.json)", "Bash(opencode *)"):
            check(rule in deny, f"settings.json: {rule} not denied")
        for rule in ("mcp__dokos__doc_create", "mcp__dokos__doc_update", "WebFetch", "WebSearch"):
            check(rule in ask, f"settings.json: {rule} not asked")
        check("mcp__dokos__doc_list" in allow, "settings.json: Dokos reads not allowed")
        check(not any(r.startswith("mcp__dokos__doc_submit") for r in allow + ask), "settings.json: doc_submit allowed or asked")
        if mail:
            check("mcp__mail__save_draft" in ask, "settings.json: mail drafts not asked")
        check(perms.get("disableBypassPermissionsMode") == "disable", "settings.json: bypass mode not disabled")
        check(settings.get("agent") == "bureau", "settings.json: the main agent is not bureau")
        hooks = settings.get("hooks", {})
        pre = hooks.get("PreToolUse", [])
        post = hooks.get("PostToolUse", [])
        check(any(g.get("matcher") == "*" and any(h.get("onFailure") == "block" and "mail_taint_guard.py" in " ".join(h.get("args", []))
                                                   for h in g.get("hooks", [])) for g in pre),
              "settings.json: no fail-closed PreToolUse guard on every tool")
        check(any(g.get("matcher") == "mcp__mail__.*" for g in post), "settings.json: no PostToolUse guard on the mail tools")
        sandbox = settings.get("sandbox", {})
        check(sandbox.get("enabled") is True and sandbox.get("allowUnsandboxedCommands") is False, "settings.json: sandbox not strict")
        check(config_root in sandbox.get("filesystem", {}).get("denyRead", []), "settings.json: sandbox can read the credentials")
        check(sandbox.get("network", {}).get("allowedDomains") == [dokos_host], "settings.json: Dokos host not reachable from the sandbox")
    bureau = frontmatter(ws / ".claude" / "agents" / "bureau.md")
    atelier = frontmatter(ws / ".claude" / "agents" / "atelier.md")
    check(bureau is not None and bureau.get("name") == "bureau", ".claude/agents/bureau.md: wrong name")
    check(atelier is not None and atelier.get("name") == "atelier", ".claude/agents/atelier.md: wrong name")
    check(atelier is not None and "mcp__mail__*" in atelier.get("disallowedTools", []), "atelier: mail tools not removed")
    guard = load_json(ws / ".claude" / "hooks" / "guard-policy.json")
    if isinstance(guard, dict):
        check("Bash" not in guard.get("safe_tools", []) and "Edit" not in guard.get("safe_tools", []), "Claude guard policy: Bash or Edit listed as safe")
        check("mcp__mail__get_emails_content" in guard.get("safe_tools", []), "Claude guard policy: mail reads not listed")


def check_skills(ws: Path, folder: str) -> None:
    for skill in SKILLS:
        path = ws / folder / skill / "SKILL.md"
        if not check(path.is_file(), f"{path}: missing"):
            continue
        fm = frontmatter(path)
        if fm is None:
            continue
        name, description = fm.get("name"), fm.get("description")
        check(name == skill, f"{path}: name {name!r} does not match its folder")
        check(isinstance(name, str) and bool(SKILL_NAME.match(name)) and len(name) <= 64, f"{path}: invalid name")
        check(isinstance(description, str) and 0 < len(description.strip()) <= 1024, f"{path}: invalid description")
        check("Ce que vous ne faites jamais" in path.read_text(encoding="utf-8"), f"{path}: no « Ce que vous ne faites jamais » section")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--fake-vars", required=True, type=Path)
    parser.add_argument("--dokos-url", required=True)
    parser.add_argument("--harness", action="append", default=[], choices=["opencode", "claude-code"])
    parser.add_argument("--opencode-version", type=int, default=2)
    parser.add_argument("--mail", action="store_true")
    parser.add_argument("--calendar", action="store_true")
    args = parser.parse_args(argv)

    ws, cfg = args.workspace, args.config_dir
    fake = yaml.safe_load(args.fake_vars.read_text(encoding="utf-8"))
    creds = fake["harness_assistant_credentials"]
    secrets = [creds["api_key"], creds["api_secret"], fake["harness_mail_password"], fake["harness_calendar_password"]]
    token_file = cfg / "dokos-token"

    # The token: only in its private file, nowhere in the workspace nor in the staging copies.
    check(token_file.read_text(encoding="utf-8") == f"{creds['api_key']}:{creds['api_secret']}", "token file content")
    check(mode(token_file) == 0o600, f"token file mode {oct(mode(token_file))}")
    check(mode(cfg) == 0o700 and mode(cfg.parent) == 0o700, "private folders are not 0700")
    for root in (ws, cfg / "rendered"):
        for path in root.rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                for secret in secrets:
                    check(secret.encode() not in content, f"{path}: contains a secret")
    if args.mail:
        config = cfg / "mail" / "config.toml"
        text = config.read_text(encoding="utf-8")
        check(mode(config) == 0o600 and mode(cfg / "mail-password") == 0o600, "mail credential files are not 0600")
        check('allowed_mutations = ["draft"]' in text, "mail account grants more than drafts")
        check("[emails.outgoing]" not in text and "smtp.example.test" not in text, "mail account has an SMTP server")
        check("allow_protocol_commands = false" in text, "mail raw protocol commands not disabled")
    else:
        check(not (cfg / "mail").exists(), "mail connector configured while disabled")
    if args.calendar:
        check(mode(cfg / "caldav-password") == 0o600, "CalDAV password file is not 0600")

    agents_md = (ws / "AGENTS.md").read_text(encoding="utf-8")
    check(args.dokos_url in agents_md and "Ce que vous ne faites jamais" in agents_md, "AGENTS.md: URL or rules missing")
    for path in [*ws.rglob("*.json"), ws / ".mcp.json"]:
        if path.is_file():
            load_json(path)

    dokos_host = re.sub(r"^https?://", "", args.dokos_url)
    if "opencode" in args.harness:
        check_opencode(ws, args.opencode_version, args.mail, args.calendar, str(token_file), args.dokos_url)
        check_skills(ws, ".agents/skills")
    else:
        check(not (ws / "opencode.json").exists() and not (ws / ".opencode").exists(), "opencode files rendered while disabled")
    if "claude-code" in args.harness:
        check_claude(ws, args.mail, args.calendar, str(token_file), str(cfg.parent), dokos_host)
        check_skills(ws, ".claude/skills")
    else:
        check(not (ws / ".mcp.json").exists() and not (ws / ".claude").exists(), "Claude Code files rendered while disabled")

    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
