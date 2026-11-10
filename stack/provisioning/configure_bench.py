# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench settings of the Compose stack, written into sites/common_site_config.json.

Run by the one-shot `configurator` service (see the compose template). It replaces
frappe_docker's `bench set-config` calls, but only writes when a value differs, so
that Ansible can tell whether anything changed. Prints one JSON object on stdout.
No secret is handled here.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BENCH = Path("/home/frappe/frappe-bench")
SITES = BENCH / "sites"
APPS = BENCH / "apps"


def desired_settings() -> dict[str, object]:
    env = os.environ
    return {
        "db_host": env["DB_HOST"],
        "db_port": int(env["DB_PORT"]),
        "redis_cache": env["REDIS_CACHE"],
        "redis_queue": env["REDIS_QUEUE"],
        # Socket.IO uses the queue instance, as in frappe_docker.
        "redis_socketio": env["REDIS_QUEUE"],
        "socketio_port": int(env["SOCKETIO_PORT"]),
        "chromium_path": env["CHROMIUM_PATH"],
    }


def write_atomically(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def ensure_common_config() -> bool:
    path = SITES / "common_site_config.json"
    current: dict[str, object] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    wanted = {**current, **desired_settings()}
    if wanted == current:
        return False
    # Same layout as `bench set-config` (frappe.installer.update_site_config).
    write_atomically(path, json.dumps(wanted, indent=1, sort_keys=True) + "\n")
    return True


def ensure_apps_txt() -> bool:
    """List the apps of the image, frappe first, like `ls -1 apps > sites/apps.txt`."""
    apps = sorted(p.name for p in APPS.iterdir() if p.is_dir() and not p.name.startswith("."))
    if "frappe" in apps:
        apps.remove("frappe")
        apps.insert(0, "frappe")
    text = "\n".join(apps)
    path = SITES / "apps.txt"
    if path.exists() and path.read_text(encoding="utf-8").split() == apps:
        return False
    write_atomically(path, text)
    return True


def main() -> int:
    config_changed = ensure_common_config()
    apps_changed = ensure_apps_txt()
    json.dump({"changed": config_changed or apps_changed, "config_changed": config_changed, "apps_changed": apps_changed}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
