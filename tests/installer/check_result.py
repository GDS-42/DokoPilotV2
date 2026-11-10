#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks, inside a test container, what install.sh and installer/wizard.py left behind.

Usage: check_result.py EXPECT.json   (run by tests/installer/run.sh with the .venv Python)

EXPECT.json describes the expected client; secret values are never in it: it names the
environment variables that hold them, and this script reads them from its environment.
  client, profile, local (bool), maintenance, provider, bureau, small, harness (list),
  network, people (list of ids), import_path
  model_key_env           variable holding the model API key (file model-api-key)
  stored_secrets          {"<key>[.<subkey>]": "<variable>"}: values expected in secrets.yml.age
  private_files           {"<file in ~/.config/dokopilot/<client>>": "<variable>"}
  secret_envs             every variable holding a secret: none may appear in clear anywhere
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

HOME = Path.home()
REPO = HOME / "DokoPilotV2"
failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    if condition:
        print(f"ok    {label}")
    else:
        failures += 1
        print(f"FAIL  {label}", file=sys.stderr)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def files_under(root: Path, skip: set[str]) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        if path.is_file() and not path.is_symlink():
            found.append(path)
    return found


def main() -> int:
    expect: dict[str, Any] = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    client = expect["client"]
    folder = REPO / "clients" / client
    settings = yaml.safe_load((folder / "client.yml").read_text(encoding="utf-8"))
    local = yaml.safe_load((folder / "client.local.yml").read_text(encoding="utf-8"))

    check(settings["profile"] == expect["profile"], f"client.yml: profile {expect['profile']}")
    check(settings["client"]["id"] == client, f"client.yml: client.id {client}")
    check("entrepreneur" not in settings["users"] and "assistants" not in settings["users"], "client.yml: users in the format of version 0.2")
    people = settings["users"]["people"]
    check([p["id"] for p in people] == expect["people"], f"client.yml: people {expect['people']}")
    check(people[0]["role"] == "dirigeant" and all(p["role"] == "membre" for p in people[1:]), "client.yml: one dirigeant, first; the others members")
    check(settings["models"]["provider"] == expect["provider"], f"client.yml: models.provider {expect['provider']}")
    check(settings["models"]["bureau"] == expect["bureau"], f"client.yml: models.bureau {expect['bureau']}")
    check(settings["models"]["small"] == expect["small"], f"client.yml: models.small {expect['small'] or '(empty: the Bureau model)'}")
    check(settings["harness"]["enabled"] == expect["harness"], f"client.yml: harness.enabled {expect['harness']}")
    check(settings["network"]["mode"] == expect["network"], f"client.yml: network.mode {expect['network']}")
    check(settings["context"]["import_path"] == expect.get("import_path", ""), "client.yml: context.import_path")
    check(local["server"]["local"] is expect["local"], f"client.local.yml: server.local {expect['local']}")
    accounts = local["maintenance"]["users"]
    check(len(accounts) == 1 and accounts[0]["name"] == expect["maintenance"], f"client.local.yml: maintenance account {expect['maintenance']}")
    keys = accounts[0].get("ssh_public_keys", []) + accounts[0].get("ssh_public_key_files", [])
    check(len(keys) > 0, "client.local.yml: the maintenance account has an SSH key")

    config_dir = HOME / ".config" / "dokopilot" / client
    key_file = config_dir / "model-api-key"
    if expect.get("model_key_env"):
        check(key_file.read_text(encoding="utf-8") == os.environ[expect["model_key_env"]], "model API key stored in ~/.config/dokopilot/<client>/model-api-key")
        check(mode(key_file) == 0o600, f"model-api-key is 0600 (found {mode(key_file):o})")
    check(mode(config_dir) == 0o700, f"~/.config/dokopilot/{client} is 0700 (found {mode(config_dir):o})")
    for name, variable in expect.get("private_files", {}).items():
        path = config_dir / name
        check(path.read_text(encoding="utf-8") == os.environ[variable] and mode(path) == 0o600, f"{name} stored, 0600")

    state_file = folder / ".assistant-etat.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    check(mode(state_file) == 0o600, "state file .assistant-etat.json is 0600")
    check("config" in state["completed"], f"state: steps done up to config ({', '.join(state['completed'])})")

    identity = HOME / ".config" / "dokopilot" / "age-identity.txt"
    clear = subprocess.run(
        ["age", "--decrypt", "--identity", str(identity), str(folder / "secrets.yml.age")], capture_output=True, text=True, check=True
    ).stdout
    stored = yaml.safe_load(clear)["secrets"]
    for dotted, variable in expect.get("stored_secrets", {}).items():
        value: Any = stored
        for part in dotted.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        check(value == os.environ[variable], f"secrets.yml.age holds {dotted}")

    # No secret in clear: client files, state, the DokoPilot copy, the other private files.
    values = [os.environ[name] for name in expect["secret_envs"]]
    candidates = files_under(REPO, {".venv", ".collections", ".git"}) + files_under(config_dir, set())
    leaks = []
    for path in candidates:
        text = path.read_bytes().decode("utf-8", errors="replace")
        for name, value in zip(expect["secret_envs"], values, strict=True):
            intended = (path == key_file and name == expect.get("model_key_env")) or any(
                path == config_dir / file and name == variable for file, variable in expect.get("private_files", {}).items()
            )
            if value in text and not intended:
                leaks.append(f"{name} in {path}")
    check(not leaks, "no secret in clear in the client files, the state file or the DokoPilot copy" + (f": {leaks}" if leaks else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
