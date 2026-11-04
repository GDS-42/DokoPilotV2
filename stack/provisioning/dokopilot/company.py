# SPDX-License-Identifier: AGPL-3.0-or-later
"""Setup wizard for a French company, run without the browser.

Calls the whitelisted `frappe.desk.page.setup_wizard.setup_wizard.setup_complete`
with the arguments the browser wizard sends (keys read in dodock and dokos, see
erpnext/tests/ui_test_fixtures.py). `make_records` swallows insert errors, so the
Company is checked afterwards; the French regional setup is enqueued after commit
(erpnext/regional/france/setup.py) and needs the queue workers.
"""

from __future__ import annotations

import datetime
from typing import Any

from dokopilot.common import StepError, recent_errors, require_site, site_connection, wait_until

FRENCH_GENERAL_CHART = "Plan Comptable Général"


def fiscal_year(start: str, today: datetime.date) -> tuple[str, str]:
    """Bounds of the fiscal year containing `today`, for a start given as MM-DD."""
    month, day = (int(part) for part in start.split("-"))
    begin = datetime.date(today.year, month, day)
    if begin > today:
        begin = datetime.date(today.year - 1, month, day)
    try:
        next_begin = begin.replace(year=begin.year + 1)
    except ValueError:  # 29 February
        next_begin = datetime.date(begin.year + 1, 3, 1)
    return begin.isoformat(), (next_begin - datetime.timedelta(days=1)).isoformat()


def setup_company(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, company_name, company_abbr, country, language, timezone, currency,
    chart_of_accounts, fiscal_year_start (MM-DD), siren, wait_timeout."""
    site = payload["site"]
    require_site(site)
    company = payload["company_name"]
    changes: list[str] = []
    with site_connection(site) as frappe:
        from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
        from frappe.utils import getdate, nowdate

        if not frappe.db.exists("Company", company):
            if frappe.db.count("Company"):
                raise StepError(
                    f"Le site contient déjà une autre société que « {company} » : DokoPilot ne crée pas "
                    "de seconde société. Vérifiez client.company_name."
                )
            language_name = frappe.db.get_value("Language", payload["language"], "language_name")
            if not language_name:
                raise StepError(f"Langue inconnue dans Dokos : {payload['language']}.")
            start, end = fiscal_year(payload["fiscal_year_start"], getdate(nowdate()))
            args = {
                "language": language_name,
                "country": payload["country"],
                "timezone": payload["timezone"],
                "currency": payload["currency"],
                "company_name": company,
                "company_abbr": payload["company_abbr"],
                "chart_of_accounts": payload.get("chart_of_accounts") or FRENCH_GENERAL_CHART,
                "fy_start_date": start,
                "fy_end_date": end,
            }
            if payload.get("siren"):
                args["siren_number"] = payload["siren"]
            setup_complete(args)
            frappe.db.commit()
            changes.append("setup wizard")

        if not frappe.db.exists("Company", company):
            raise StepError(
                f"L'assistant de configuration n'a pas créé la société « {company} ». "
                f"Derniers journaux d'erreur Dokos : {', '.join(recent_errors()) or 'aucun'}."
            )

        if not frappe.is_setup_complete():
            # Same fallback as Dokos' own fixture (erpnext/tests/ui_test_fixtures.py,
            # force_setup_complete): the per-app stage flags are not always set.
            for app_name in ("frappe", "erpnext"):
                frappe.db.set_value("Installed Application", {"app_name": app_name}, "is_setup_complete", 1)
            frappe.db.set_single_value("System Settings", "setup_complete", 1)
            frappe.db.commit()
            frappe.clear_cache()
            changes.append("setup flags")

        values = frappe.db.get_value(
            "Company", company, ["country", "default_currency", "chart_of_accounts", "abbr"], as_dict=True
        )
        if payload.get("siren") and frappe.db.get_value("Company", company, "siren_number") != payload["siren"]:
            frappe.db.set_value("Company", company, "siren_number", payload["siren"])
            changes.append("siren")

        def regional_setup_done() -> bool:
            if values.country != "France":
                return True
            if not frappe.db.exists("France Tax Settings", company):
                return False
            if values.chart_of_accounts == FRENCH_GENERAL_CHART:
                return bool(frappe.db.count("Journal Entry Template", {"company": company}))
            return True

        if not wait_until(regional_setup_done, int(payload.get("wait_timeout", 300))):
            raise StepError(
                "La configuration régionale française (réglages de TVA, modèles d'écritures) n'a pas été "
                "faite : elle tourne en tâche de fond. Vérifiez que les conteneurs queue-short et "
                "queue-long fonctionnent (docker compose ps), puis relancez."
            )
        accounts = frappe.db.count("Account", {"company": company})
        taxes = frappe.get_all("Sales Taxes and Charges Template", filters={"company": company}, pluck="name")
        fiscal_years = frappe.get_all("Fiscal Year", pluck="name")

    return {
        "changed": bool(changes),
        "changes": changes,
        "company": company,
        "chart_of_accounts": values.chart_of_accounts,
        "currency": values.default_currency,
        "accounts": accounts,
        "sales_tax_templates": taxes,
        "fiscal_years": fiscal_years,
    }
