# SPDX-License-Identifier: AGPL-3.0-or-later
"""Restore a `bench backup --with-files` set into the existing site.

Runs frappe.commands.site._restore, the function behind `bench --site <site> restore`,
so that the database root password comes from stdin instead of the command line.
`bench migrate` is run afterwards by /usr/local/sbin/dokopilot-restore.

When the backup carries the site configuration, its `encryption_key` is put back first:
without it, the encrypted fields of the restored database (API secrets, passwords of
integrations) could not be read, for example after a restore on a new server.
"""

from __future__ import annotations

import json
import os
from typing import Any

from dokopilot.common import SITES_PATH, StepError, require_site


def restore(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, db_root_password, sql_file, public_file, private_file, site_config_file."""
    import frappe
    from frappe.commands.site import _restore
    from frappe.installer import update_site_config

    site = payload["site"]
    require_site(site)
    for key in ("sql_file", "public_file", "private_file"):
        path = payload.get(key)
        if key == "sql_file" and not path:
            raise StepError("Aucun fichier de base de données dans la sauvegarde.")
        if path and not os.path.exists(path):
            raise StepError(f"Fichier de sauvegarde introuvable : {path}")

    os.chdir(SITES_PATH)
    frappe.init(site=site, sites_path=SITES_PATH)
    changes: list[str] = []
    try:
        config_file = payload.get("site_config_file")
        if config_file and os.path.exists(config_file):
            with open(config_file, encoding="utf-8") as handle:
                backed_up = json.load(handle)
            key = backed_up.get("encryption_key")
            if key and key != frappe.conf.get("encryption_key"):
                update_site_config("encryption_key", key)
                changes.append("encryption_key")
                frappe.destroy()
                frappe.init(site=site, sites_path=SITES_PATH)
        try:
            _restore(
                site=site,
                sql_file_path=payload["sql_file"],
                db_root_username="root",
                db_root_password=payload["db_root_password"],
                force=True,
                with_public_files=payload.get("public_file") or None,
                with_private_files=payload.get("private_file") or None,
            )
        except SystemExit as error:
            raise StepError(f"La restauration de la base a échoué (code {error.code}).") from None
        changes.append("database and files restored")
    finally:
        frappe.destroy()
    return {"changed": True, "changes": changes}
