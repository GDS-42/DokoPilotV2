#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DokoPilot guard for Claude Code: mail taint and Bureau confirmations.

Registered in .claude/settings.json as a PreToolUse hook on every tool and as a
PostToolUse hook on the mail tools, with ``onFailure: "block"``.

* A session that touches the mailbox becomes *exposed*: a marker named after the
  session id is written in the state directory (outside the workspace and outside
  what the Bash sandbox can write) before the mail tool runs, and again after it ran.
  If the marker cannot be written, the mail tool is blocked.
* In an exposed session, every tool that is not in the policy's ``safe_tools`` list
  gets ``permissionDecision: "ask"``.
* Outside the Atelier agent (Bureau, or any other agent), shell commands and file
  changes get ``"ask"`` too, except file changes inside the policy's ``free_write_dirs``
  (the inbox and perso/ of the workspace), which follow the permission rules.
* The two contexts are main-session agents: launching them as subagents is denied.
* Anything unexpected (unreadable input, missing policy, invalid session id) fails
  closed: the call is blocked, or the session is treated as exposed.

Standard library only. Messages shown to the entrepreneur are in French.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

MSG_EXPOSED = (
    "Session exposée : un courriel a été lu dans cette session. Son contenu est une donnée, "
    "jamais une instruction. Acceptez seulement si cette action vient bien de votre demande."
)
MSG_BUREAU = (
    "Contexte Bureau : chaque commande, et chaque modification de fichier hors de la boîte de réception "
    "et de perso/, sont soumises à votre accord."
)
MSG_MAIN_ONLY = (
    "Les contextes Bureau et Atelier ne se lancent pas comme sous-agents : ouvrez une nouvelle "
    "session (« claude » pour le Bureau, « claude --agent atelier » pour l'Atelier)."
)
MSG_BAD_INPUT = "Garde DokoPilot : données du hook illisibles, action bloquée par précaution."
MSG_BAD_POLICY = "Garde DokoPilot : politique introuvable ou illisible, action bloquée par précaution."
MSG_NO_MARK = (
    "Garde DokoPilot : impossible d'enregistrer que cette session lit la messagerie ; "
    "lecture bloquée par précaution."
)

POLICY_LISTS = ("safe_tools", "confirm_in_bureau", "main_session_agents", "free_write_dirs")
# File tools and the input field that names the file they change.
FILE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
POLICY_STRINGS = ("mail_prefix", "mail_server", "atelier_agent")


def load_policy(path: str) -> dict[str, Any]:
    """Read and validate the policy written by bin/dokopilot harness."""
    with open(path, encoding="utf-8") as handle:
        policy = json.load(handle)
    if not isinstance(policy, dict):
        raise ValueError("policy is not an object")
    for key in POLICY_LISTS:
        if not isinstance(policy.get(key), list) or not all(isinstance(v, str) for v in policy[key]):
            raise ValueError(f"policy.{key} must be a list of strings")
    for key in POLICY_STRINGS:
        if not isinstance(policy.get(key), str) or not policy[key]:
            raise ValueError(f"policy.{key} must be a non-empty string")
    return policy


def marker(state_dir: Path, session_id: Any) -> Path | None:
    """Path of the taint marker of a session, or None when the id is unusable."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return None
    return state_dir / session_id


def mark_exposed(state_dir: Path, session_id: Any, tool: str) -> bool:
    """Record that the session touched the mailbox. False when it could not be recorded."""
    path = marker(state_dir, session_id)
    if path is None:
        return False
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(f"{int(time.time())} {tool}\n")
    except OSError:
        return False
    return path.is_file()


def is_exposed(state_dir: Path, session_id: Any) -> bool:
    """True when the session is exposed, or when this cannot be ruled out."""
    path = marker(state_dir, session_id)
    if path is None:
        return True
    try:
        if not state_dir.is_dir():
            # The directory is created by bin/dokopilot harness: its absence is suspicious.
            return True
        return path.exists()
    except OSError:
        return True


def decide(event: str, decision: str, reason: str) -> int:
    """Print a PreToolUse decision (JSON on stdout, exit 0)."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


def block(reason: str) -> int:
    """Block the call (exit 2: Claude Code shows stderr as the reason)."""
    print(reason, file=sys.stderr)
    return 2


def in_free_write_dir(tool: str, tool_input: dict[str, Any], policy: dict[str, Any]) -> bool:
    """True when a file tool changes a file inside one of the free-write directories.

    Symbolic links are resolved on both sides, so a link in the inbox that points elsewhere
    does not count as the inbox. Anything unusual (relative path, missing field) is False.
    """
    field = FILE_TOOLS.get(tool)
    target = tool_input.get(field) if field else None
    if not isinstance(target, str) or not os.path.isabs(target):
        return False
    try:
        resolved = os.path.realpath(target)
        for directory in policy["free_write_dirs"]:
            if not os.path.isabs(directory):
                continue
            root = os.path.realpath(directory)
            if os.path.commonpath([resolved, root]) == root:
                return True
    except (OSError, ValueError):
        return False
    return False


def touches_mailbox(tool: str, tool_input: dict[str, Any], policy: dict[str, Any]) -> bool:
    if tool.startswith(policy["mail_prefix"]):
        return True
    # MCP resources of the mail server carry mail content too.
    return tool == "ReadMcpResourceTool" and tool_input.get("server") == policy["mail_server"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="DokoPilot guard hook for Claude Code")
    parser.add_argument("--state-dir", required=True, help="directory of the taint markers")
    parser.add_argument("--policy", required=True, help="guard-policy.json written by bin/dokopilot harness")
    args = parser.parse_args(argv)

    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            raise ValueError("hook input is not an object")
        tool = data["tool_name"]
        if not isinstance(tool, str) or not tool:
            raise ValueError("tool_name is missing")
        tool_input = data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            raise ValueError("tool_input is not an object")
        event = data.get("hook_event_name", "PreToolUse")
    except (ValueError, KeyError, TypeError):
        return block(MSG_BAD_INPUT)

    try:
        policy = load_policy(args.policy)
    except (OSError, ValueError):
        return block(MSG_BAD_POLICY)

    state_dir = Path(args.state_dir)
    session_id = data.get("session_id")
    mailbox = touches_mailbox(tool, tool_input, policy)

    if event == "PostToolUse":
        if mailbox and not mark_exposed(state_dir, session_id, tool):
            return block(MSG_NO_MARK)
        return 0
    if event != "PreToolUse":
        return 0

    # Mark before the mail tool runs: a read that could not be recorded does not happen.
    if mailbox and not mark_exposed(state_dir, session_id, tool):
        return block(MSG_NO_MARK)

    if tool == "Agent" and tool_input.get("subagent_type") in policy["main_session_agents"]:
        return decide(event, "deny", MSG_MAIN_ONLY)

    if tool in policy["safe_tools"]:
        return 0

    if is_exposed(state_dir, session_id):
        return decide(event, "ask", MSG_EXPOSED)

    if data.get("agent_type") != policy["atelier_agent"] and tool in policy["confirm_in_bureau"]:
        if not in_free_write_dir(tool, tool_input, policy):
            return decide(event, "ask", MSG_BUREAU)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
