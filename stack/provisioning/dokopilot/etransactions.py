# SPDX-License-Identifier: AGPL-3.0-or-later
"""eTransactions settings for receiving e-invoices.

Purchase invoices created from received e-invoices are never submitted automatically:
`auto_submit_purchase_invoices` is forced to 0 on every run (the human validates).
A Super PDP platform record is created only when credentials are given; without one,
nothing is polled (etransactions flow.poll_incoming_flows) and received e-invoices can
still be imported by hand or by the demo data.
"""

from __future__ import annotations

from typing import Any

from dokopilot.common import StepError, require_site, site_connection

PLATFORM_NAME = "Super PDP"


def _ensure_platform(frappe: Any, company: str, credentials: dict[str, Any], sandbox: bool) -> list[str]:
    changes: list[str] = []
    name = frappe.db.get_value(
        "eTransactions Accredited Platform", {"company": company, "platform_type": "SuperPDP"}, "name"
    )
    if name:
        platform = frappe.get_doc("eTransactions Accredited Platform", name)
    else:
        platform = frappe.new_doc("eTransactions Accredited Platform")
        # Receiving only by default; the entrepreneur may extend it to sales in Dokos.
        platform.update(
            {
                "platform_name": PLATFORM_NAME,
                "platform_type": "SuperPDP",
                "company": company,
                "transaction_type": "Purchases",
                "is_active": 1,
                "auth_method": "client_credentials",
                "auto_send_on_submit": 0,
            }
        )
    wanted = {"is_sandbox": 1 if sandbox else 0}
    if credentials.get("api_base_url"):
        wanted["api_base_url"] = credentials["api_base_url"]
    differing = {field: value for field, value in wanted.items() if platform.get(field) != value}
    for field in ("client_id", "client_secret"):
        current = platform.get_password(field, raise_exception=False) if not platform.is_new() else None
        if current != credentials[field]:
            differing[field] = credentials[field]
    if platform.is_new() or differing:
        platform.update(differing)
        platform.flags.ignore_permissions = True
        platform.save()
        changes.append("platform created" if not name else f"platform: {', '.join(sorted(differing))}")
    return changes


def configure(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, company, enabled, environment (sandbox|production), credentials."""
    site = payload["site"]
    require_site(site)
    changes: list[str] = []
    with site_connection(site) as frappe:
        if not frappe.db.exists("DocType", "eTransactions Settings"):
            raise StepError("L'application eTransactions n'est pas installée sur le site.")
        settings = frappe.get_single("eTransactions Settings")
        wanted: dict[str, int] = {"auto_submit_purchase_invoices": 0}
        if payload.get("enabled"):
            wanted.update({"enable_einvoicing": 1, "enable_receiving_einvoices": 1})
        differing = {field: value for field, value in wanted.items() if int(settings.get(field) or 0) != value}
        if differing:
            settings.update(differing)
            settings.flags.ignore_permissions = True
            settings.save()
            changes.append(f"settings: {', '.join(sorted(differing))}")

        credentials = payload.get("credentials") or {}
        platform = "none"
        if payload.get("enabled") and credentials.get("client_id") and credentials.get("client_secret"):
            changes += _ensure_platform(frappe, payload["company"], credentials, payload.get("environment") != "production")
            platform = "superpdp-sandbox" if payload.get("environment") != "production" else "superpdp-production"

        settings = frappe.get_single("eTransactions Settings")
        state = {
            field: int(settings.get(field) or 0)
            for field in ("enable_einvoicing", "enable_receiving_einvoices", "enable_sending_einvoices", "auto_submit_purchase_invoices")
        }
    return {"changed": bool(changes), "changes": changes, "settings": state, "platform": platform}
