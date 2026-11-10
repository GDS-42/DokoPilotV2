# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only health and safety checks of the site (used by check.yml, also in production).

Nothing is written. Problems are reported in French, for the integrator.
"""

from __future__ import annotations

from typing import Any

from dokopilot.common import require_site, site_connection


def run_checks(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, company, apps, assistants, assistant_roles, forbidden_roles,
    critical_doctypes, forbidden_ptypes, pos_doctypes, min_workers."""
    site = payload["site"]
    require_site(site)
    problems: list[str] = []
    with site_connection(site) as frappe:
        from frappe.utils.background_jobs import get_workers
        from frappe.utils.scheduler import is_scheduler_inactive

        installed = frappe.get_installed_apps()
        versions = {app: frappe.get_attr(f"{app}.__version__") for app in installed}
        for app in payload["apps"]:
            if app not in installed:
                problems.append(f"application {app} absente du site")
        for app in ("certification_caisse", "bank"):
            if app in installed:
                problems.append(f"application {app} installée alors qu'elle est hors périmètre")

        if not frappe.db.exists("Company", payload["company"]):
            problems.append(f"société « {payload['company']} » absente")
        if not frappe.is_setup_complete():
            problems.append("assistant de configuration non terminé")

        workers = len(get_workers())
        if workers < int(payload.get("min_workers", 2)):
            problems.append(f"{workers} worker(s) de tâches de fond actif(s), {payload.get('min_workers', 2)} attendus")
        if is_scheduler_inactive():
            problems.append("planificateur (scheduler) inactif")
        if frappe.get_conf().get("server_script_enabled"):
            problems.append("server_script_enabled est activé : les scripts serveur ne doivent pas l'être par défaut")

        mcp = {
            "enable_mcp_server": frappe.db.get_single_value("AI Settings", "enable_mcp_server"),
            "mcp_allow_writes": frappe.db.get_single_value("AI Settings", "mcp_allow_writes"),
        }
        if not mcp["enable_mcp_server"]:
            problems.append("serveur MCP de Dokos AI désactivé")
        if frappe.db.get_single_value("eTransactions Settings", "auto_submit_purchase_invoices"):
            problems.append("eTransactions valide automatiquement les factures d'achat (auto_submit_purchase_invoices)")

        assistants: dict[str, Any] = {}
        allowed_roles = set(payload["assistant_roles"])
        for email in payload["assistants"]:
            if not frappe.db.exists("User", email):
                problems.append(f"assistant {email} absent")
                continue
            stored_roles = set(frappe.get_all("Has Role", filters={"parent": email, "parenttype": "User"}, pluck="role"))
            roles = set(frappe.get_roles(email))
            forbidden_roles = sorted(roles & set(payload["forbidden_roles"]))
            extra_roles = sorted(stored_roles - allowed_roles)
            granted: list[str] = []
            for doctype in payload["critical_doctypes"]:
                if not frappe.db.exists("DocType", doctype):
                    continue
                for ptype in payload["forbidden_ptypes"]:
                    if frappe.has_permission(doctype, ptype, user=email):
                        granted.append(f"{doctype}:{ptype}")
            pos_readable = [
                doctype
                for doctype in payload["pos_doctypes"]
                if frappe.db.exists("DocType", doctype) and frappe.has_permission(doctype, "read", user=email)
            ]
            assistants[email] = {
                "enabled": bool(frappe.db.get_value("User", email, "enabled")),
                "roles": sorted(stored_roles),
                "has_api_key": bool(frappe.db.get_value("User", email, "api_key")),
                "forbidden_roles": forbidden_roles,
                "extra_roles": extra_roles,
                "forbidden_permissions": granted,
                "pos_readable": pos_readable,
            }
            if forbidden_roles:
                problems.append(f"{email} détient des rôles interdits : {', '.join(forbidden_roles)}")
            if extra_roles:
                problems.append(f"{email} détient des rôles en trop : {', '.join(extra_roles)}")
            if granted:
                problems.append(f"{email} peut valider, annuler, amender ou supprimer : {', '.join(granted)}")
            if pos_readable:
                problems.append(f"{email} peut lire des documents de caisse : {', '.join(pos_readable)}")

        # Supporting documents: the attachments of a submitted document cannot be deleted.
        unprotected = [
            doctype
            for doctype in payload.get("protected_attachment_doctypes") or []
            if frappe.db.exists("DocType", doctype) and not frappe.get_meta(doctype).protect_attached_files
        ]
        if unprotected:
            problems.append(
                "les pièces jointes des documents validés peuvent être supprimées (propriété « Protéger les fichiers "
                f"joints » absente) : {', '.join(unprotected)}"
            )

    return {
        "changed": False,
        "ok": not problems,
        "problems": problems,
        "versions": versions,
        "workers": workers,
        "mcp": mcp,
        "assistants": assistants,
    }
