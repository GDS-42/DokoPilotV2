#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Claude Code `headersHelper` of the Dokos MCP server.

Claude Code runs this command when it connects to the `dokos` server (.mcp.json), outside
the Bash sandbox and only once the workspace is trusted. It prints the Authorization header
built from the private token file, so the token never appears in .mcp.json, in an
environment variable or on a command line.

Usage: dokos_headers.py <token file>   (the file holds "<api_key>:<api_secret>")
Standard library only.
"""

from __future__ import annotations

import json
import re
import sys

TOKEN = re.compile(r"^[A-Za-z0-9]+:[A-Za-z0-9]+$")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Usage : dokos_headers.py <fichier du jeton>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError as error:
        print(f"Jeton Dokos illisible ({error.strerror}) : relancez « bin/dokopilot harness ».", file=sys.stderr)
        return 1
    if not TOKEN.match(token):
        print("Jeton Dokos mal formé : relancez « bin/dokopilot harness ».", file=sys.stderr)
        return 1
    print(json.dumps({"Authorization": f"token {token}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
