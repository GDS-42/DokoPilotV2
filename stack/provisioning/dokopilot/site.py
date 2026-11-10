# SPDX-License-Identifier: AGPL-3.0-or-later
"""Site creation, apps, site settings and the image marker used to trigger migrations.

`bench new-site` takes the database root password and the Administrator password as
command-line options; calling `frappe.installer._new_site` (what the command runs,
dodock frappe/commands/site.py) keeps them off the process list.
"""

from __future__ import annotations

import os
from typing import Any

from dokopilot.common import SITES_PATH, StepError, require_site, site_connection, site_exists

IMAGE_MARKER = "dokopilot_image"


def _create_site(payload: dict[str, Any]) -> None:
    import frappe
    from frappe.installer import _new_site

    site = payload["site"]
    if os.path.exists(os.path.join(SITES_PATH, site)):
        raise StepError(
            f"Le dossier du site « {site} » existe sans site_config.json : une création précédente a "
            "échoué. Supprimez ce dossier du volume « sites » (et sa base de données) avant de relancer."
        )
    os.chdir(SITES_PATH)
    # As `bench new-site` does before calling _new_site (frappe/commands/site.py).
    frappe.init(site=site, sites_path=SITES_PATH, new_site=True)
    try:
        _new_site(
            None,
            site,
            db_root_username="root",
            db_root_password=payload["db_root_password"],
            admin_password=payload["admin_password"],
            install_apps=list(payload["apps"]),
            mariadb_user_host_login_scope="%",
        )
    except SystemExit as error:  # _new_site exits on some refusals
        raise StepError(f"La création du site « {site} » a échoué (code {error.code}).") from None
    finally:
        frappe.destroy()


def ensure_site(payload: dict[str, Any]) -> dict[str, Any]:
    """Create the site if needed, install missing apps, apply site settings.

    Payload: site, apps, db_root_password, admin_password, host_name, image.
    """
    site = payload["site"]
    created = False
    if not site_exists(site):
        _create_site(payload)
        created = True

    changes: list[str] = ["site created"] if created else []
    with site_connection(site) as frappe:
        from frappe.installer import install_app, update_site_config
        from frappe.utils.password import check_password, update_password

        installed = frappe.get_installed_apps()
        for app in payload["apps"]:
            if app not in installed:
                install_app(app, set_as_patched=True)
                changes.append(f"app {app} installed")
        missing = [app for app in payload["apps"] if app not in frappe.get_installed_apps()]
        if missing:
            raise StepError(f"Applications non installées sur le site : {', '.join(missing)}.")

        host_name = payload.get("host_name") or ""
        if host_name and frappe.conf.get("host_name") != host_name:
            update_site_config("host_name", host_name)
            changes.append("host_name")

        if not frappe.db.get_single_value("System Settings", "enable_scheduler"):
            from frappe.utils.scheduler import enable_scheduler

            enable_scheduler()
            changes.append("scheduler enabled")

        if not created:
            try:
                check_password("Administrator", payload["admin_password"], delete_tracker_cache=True)
            except frappe.AuthenticationError:
                update_password("Administrator", payload["admin_password"])
                changes.append("Administrator password")

        needs_migrate = not created and frappe.conf.get(IMAGE_MARKER) != payload["image"]
        if created:
            update_site_config(IMAGE_MARKER, payload["image"])

        versions = {app: frappe.get_attr(f"{app}.__version__") for app in frappe.get_installed_apps()}

    return {
        "changed": bool(changes),
        "changes": changes,
        "created": created,
        "needs_migrate": needs_migrate,
        "versions": versions,
    }


def mark_image(payload: dict[str, Any]) -> dict[str, Any]:
    """Record the image the site was migrated with (after `bench migrate`)."""
    site = payload["site"]
    require_site(site)
    with site_connection(site) as frappe:
        from frappe.installer import update_site_config

        if frappe.conf.get(IMAGE_MARKER) == payload["image"]:
            return {"changed": False}
        update_site_config(IMAGE_MARKER, payload["image"])
    return {"changed": True}
