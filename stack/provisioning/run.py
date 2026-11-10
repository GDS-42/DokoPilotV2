# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point of the provisioning steps, run inside the backend container.

    env/bin/python /opt/dokopilot/provisioning/run.py <step>  < payload.json

The payload (which may hold secrets) is read from stdin, never from the command line.
The result is one JSON object on stdout: {"changed": bool, ...} on success, or
{"failed": true, "msg": "..."} with exit code 1. Frappe's own progress output is sent
to stderr so that stdout only carries the result. Secrets found in the payload are
masked in the failure message.
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The demo data and its loader (demo/ of the repository), mounted for the demo profile.
DEMO_DIR = Path("/opt/dokopilot/demo")
if (DEMO_DIR / "demo_loader.py").exists():
    sys.path.insert(1, str(DEMO_DIR))

from dokopilot.common import StepError  # noqa: E402 (after the sys.path setup)

STEPS: dict[str, tuple[str, str]] = {
    "ensure-site": ("dokopilot.site", "ensure_site"),
    "mark-image": ("dokopilot.site", "mark_image"),
    "setup-company": ("dokopilot.company", "setup_company"),
    "entrepreneur": ("dokopilot.users", "ensure_entrepreneur"),
    "assistant-role": ("dokopilot.permissions", "ensure_assistant_role"),
    "assistants": ("dokopilot.users", "ensure_assistants"),
    "dokos-ai": ("dokopilot.dokos_ai", "configure"),
    "etransactions": ("dokopilot.etransactions", "configure"),
    "checks": ("dokopilot.checks", "run_checks"),
    "restore": ("dokopilot.restore", "restore"),
    "demo-load": ("demo_loader", "load"),
    "demo-reset": ("demo_loader", "reset"),
}

SECRET_HINTS = ("password", "secret", "api_key", "token", "key")


def secret_values(value: Any, key: str = "", inherited: bool = False) -> list[str]:
    """Values of the payload that must never be printed: those under a key that names a
    secret, at any depth. « provider_keys: {mistral: "..."} » holds a secret under a key
    that does not look like one: the hint of the enclosing key is inherited (for values of
    8 characters or more, so that a short ordinary word is not masked everywhere)."""
    hinted = any(hint in key.lower() for hint in SECRET_HINTS)
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in secret_values(v, str(k), inherited or hinted)]
    if isinstance(value, list):
        return [s for item in value for s in secret_values(item, key, inherited)]
    if isinstance(value, str) and ((hinted and len(value) >= 4) or (inherited and len(value) >= 8)):
        return [value]
    return []


def mask(text: str, secrets: list[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in STEPS:
        sys.stderr.write(f"usage: run.py <{'|'.join(STEPS)}>\n")
        return 2
    raw = sys.stdin.read()
    payload: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    secrets = secret_values(payload)
    result_stream = sys.stdout
    sys.stdout = sys.stderr
    code = 0
    try:
        module_name, function_name = STEPS[sys.argv[1]]
        function = getattr(importlib.import_module(module_name), function_name)
        result = function(payload)
    except StepError as error:
        result = {"failed": True, "msg": mask(str(error), secrets)}
        code = 1
    except Exception as error:  # report anything else without leaking a secret
        sys.stderr.write(mask(traceback.format_exc(), secrets))
        message = f"Erreur inattendue pendant l'étape {sys.argv[1]} : {type(error).__name__}: {error}"
        result = {"failed": True, "msg": mask(message, secrets)[:2000]}
        code = 1
    finally:
        sys.stdout = result_stream
    result_stream.write(json.dumps(result, default=str, ensure_ascii=False) + "\n")
    result_stream.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
