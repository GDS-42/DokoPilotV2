#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start the CalDAV MCP server for Claude Code with its password read from a private file.

.mcp.json can only expand environment variables, so the password would otherwise have to
sit in the configuration or in the environment that the agent's shell inherits. This
launcher reads it from ~/.config/dokopilot/<client>/caldav-password and gives it to the MCP
server process only (which removes it from its own environment once read).

Usage: caldav_launcher.py <password file> <command> [arguments...]
Standard library only.
"""

from __future__ import annotations

import os
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage : caldav_launcher.py <fichier du mot de passe> <commande> [arguments…]", file=sys.stderr)
        return 2
    password_file, command = argv[0], argv[1:]
    try:
        with open(password_file, encoding="utf-8") as handle:
            password = handle.read().rstrip("\r\n")
    except OSError as error:
        print(f"Mot de passe d'agenda illisible ({error.strerror}) : relancez « bin/dokopilot harness ».", file=sys.stderr)
        return 1
    if not password:
        print("Mot de passe d'agenda vide : relancez « bin/dokopilot harness ».", file=sys.stderr)
        return 1
    env = dict(os.environ)
    env["CALDAV_PASSWORD"] = password
    try:
        os.execvpe(command[0], command, env)
    except OSError as error:
        print(f"Impossible de lancer {command[0]} ({error.strerror}) : installez Node.js 24 ou plus récent.", file=sys.stderr)
        return 127
    return 0  # not reached: execvpe replaces this process


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
