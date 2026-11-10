#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Claude Code `apiKeyHelper` (models.claude_code_auth: api_key).

Claude Code runs this command itself, outside the Bash sandbox, only once the workspace is
trusted, and sends what it prints as the API key of the model requests. The key is read from
the private file ~/.config/dokopilot/<client>/model-api-key (0600), which the agent cannot
read: it never appears in .claude/settings.json, in an environment variable or on a command
line. With models.claude_code_auth: subscription, no helper is configured (/login).

Usage: model_key_helper.py <key file>
Standard library only. Prints nothing but the key on stdout.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Usage : model_key_helper.py <fichier de la clé>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as handle:
            key = handle.read().strip()
    except OSError as error:
        print(f"Clé du fournisseur de modèle illisible ({error.strerror}) : relancez « bin/dokopilot harness ».", file=sys.stderr)
        return 1
    if not key or not key.isprintable() or any(c.isspace() for c in key):
        print("Clé du fournisseur de modèle vide ou mal formée : relancez « bin/dokopilot harness ».", file=sys.stderr)
        return 1
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
