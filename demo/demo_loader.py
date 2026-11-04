# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loads the FICTITIOUS « Atelier Démo » test data into a Dokos site (demo profile only).

Run inside the backend container through stack/provisioning/run.py (steps `demo-load`
and `demo-reset`), with the data of demo/data/atelier-demo.yml, the bank statement
demo/bank/releve-demo.csv and the e-invoice generator demo/einvoices/generate.py.

Everything goes through the documented document APIs (insert, submit, the ERPNext
mappers make_purchase_receipt and get_payment_entry) and, for received e-invoices,
through eTransactions' incoming path: a private File and an incoming eInvoice, which
the app parses into a Supplier Invoice to process (as in etransactions'
test_einvoice.py). Dates are offsets from the loading day. The whole load is one
transaction: on failure nothing is kept, and a site marker makes a second load a no-op.
"""

from __future__ import annotations

import csv
import datetime
import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "einvoices"))

import generate  # noqa: E402

from dokopilot.common import SITES_PATH, StepError, require_site, site_connection  # noqa: E402

MARKER = "dokopilot_demo_loaded"
DATA_FILE = HERE / "data" / "atelier-demo.yml"


class Loader:
    def __init__(self, frappe: Any, company: str, data: dict[str, Any], today: datetime.date) -> None:
        self.frappe = frappe
        self.company = company
        self.data = data
        self.today = today
        self.abbr = frappe.get_cached_value("Company", company, "abbr")
        self.items = {item["code"]: item for item in data["articles"]}
        self.suppliers = {supplier["nom"]: supplier for supplier in data["fournisseurs"]}
        self.purchase_orders: dict[str, str] = {}

    # ------------------------------------------------------------------ lookups

    def day(self, offset: int) -> datetime.date:
        return self.today + datetime.timedelta(days=int(offset))

    def leaf(self, doctype: str, wanted: str | None) -> str:
        """`wanted` if it is a leaf of the tree, else the first leaf (names follow the language)."""
        if wanted and self.frappe.db.exists(doctype, {"name": wanted, "is_group": 0}):
            return wanted
        found = self.frappe.db.get_value(doctype, {"is_group": 0}, "name", order_by="lft")
        if not found:
            raise StepError(f"Aucun élément « {doctype} » utilisable dans Dokos.")
        return found

    def warehouse(self) -> str:
        for name in ("Magasins", "Stores"):
            found = self.frappe.db.get_value("Warehouse", {"company": self.company, "warehouse_name": name, "is_group": 0})
            if found:
                return found
        return self.frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 0}, "name", order_by="lft")

    def tax_template(self, doctype: str, title: str) -> str | None:
        return self.frappe.db.get_value(doctype, {"company": self.company, "title": title}) or self.frappe.db.get_value(
            doctype, {"company": self.company, "is_default": 1}
        )

    def account(self, number: str) -> str:
        found = self.frappe.db.get_value("Account", {"company": self.company, "account_number": number, "is_group": 0})
        if not found:
            raise StepError(f"Compte {number} introuvable dans le plan comptable de {self.company}.")
        return found

    def with_taxes(self, doc: Any, doctype: str, template: str | None) -> Any:
        from erpnext.controllers.accounts_controller import get_taxes_and_charges

        if template:
            doc.taxes_and_charges = template
            doc.set("taxes", get_taxes_and_charges(doctype, template))
        return doc

    # ------------------------------------------------------------------ masters

    def company_details(self) -> None:
        details = self.data["societe"]
        company = self.frappe.get_doc("Company", self.company)
        changed = False
        for field, value in (("siren_number", details["siren"]), ("tax_id", details["tva"]), ("email", details.get("courriel")), ("phone_no", details.get("telephone"))):
            if value and company.meta.has_field(field) and not company.get(field):
                company.set(field, value)
                changed = True
        if changed:
            company.save()
        if not self.frappe.db.exists("Dynamic Link", {"link_doctype": "Company", "link_name": self.company, "parenttype": "Address"}):
            self.address(self.company, "Company", self.company, details["adresse"], details.get("courriel"), is_company=True)

    def address(self, title: str, link_doctype: str, link_name: str, address: dict[str, str], email: str | None = None, is_company: bool = False) -> None:
        self.frappe.get_doc(
            {
                "doctype": "Address",
                "address_title": title,
                "address_type": "Billing",
                "address_line1": address["ligne1"],
                "pincode": address["code_postal"],
                "city": address["ville"],
                "country": "France",
                "email_id": email,
                "is_primary_address": 1,
                "is_your_company_address": 1 if is_company else 0,
                "links": [{"link_doctype": link_doctype, "link_name": link_name}],
            }
        ).insert()

    def contact(self, name: str, link_doctype: str, link_name: str, email: str) -> None:
        self.frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": name,
                "email_ids": [{"email_id": email, "is_primary": 1}],
                "links": [{"link_doctype": link_doctype, "link_name": link_name}],
            }
        ).insert()

    def fiscal_years(self) -> None:
        """Make sure a fiscal year covers every date of the demo (an install in January
        needs the previous year)."""
        from erpnext.accounts.utils import FiscalYearError, get_fiscal_year

        reference = self.frappe.get_all("Fiscal Year", fields=["year_start_date"], order_by="year_start_date desc", limit=1)
        if not reference:
            raise StepError("Aucun exercice comptable : l'assistant de configuration n'a pas abouti.")
        start = reference[0].year_start_date
        for offset in range(-120, 61, 15):
            date = self.day(offset)
            try:
                get_fiscal_year(date, company=self.company)
                continue
            except FiscalYearError:
                pass
            begin = start.replace(year=date.year if (date.month, date.day) >= (start.month, start.day) else date.year - 1)
            end = begin.replace(year=begin.year + 1) - datetime.timedelta(days=1)
            label = str(begin.year) if begin.year == end.year else f"{begin.year}-{end.year}"
            if not self.frappe.db.exists("Fiscal Year", label):
                self.frappe.get_doc({"doctype": "Fiscal Year", "year": label, "year_start_date": begin, "year_end_date": end}).insert()

    def masters(self) -> None:
        frappe = self.frappe
        warehouse = self.warehouse()
        selling = frappe.db.get_single_value("Selling Settings", "selling_price_list")
        buying = frappe.db.get_single_value("Buying Settings", "buying_price_list")
        for item in self.data["articles"]:
            if frappe.db.exists("Item", item["code"]):
                continue
            doc = frappe.get_doc(
                {
                    "doctype": "Item",
                    "item_code": item["code"],
                    "item_name": item["nom"],
                    "description": item["nom"],
                    "item_group": self.leaf("Item Group", item["groupe"]),
                    "stock_uom": item["unite"],
                    "is_stock_item": 1 if item["stock"] else 0,
                    "include_item_in_manufacturing": 0,
                    "valuation_rate": item.get("achat") or round(float(item.get("vente", 0)) * 0.4, 2),
                    "item_defaults": [{"company": self.company, "default_warehouse": warehouse if item["stock"] else None}],
                }
            )
            doc.insert()
            for price_list, rate in ((selling, item.get("vente")), (buying, item.get("achat"))):
                if price_list and rate:
                    frappe.get_doc({"doctype": "Item Price", "item_code": item["code"], "price_list": price_list, "price_list_rate": rate}).insert()

        territory = self.leaf("Territory", "France")
        for customer in self.data["clients"]:
            if frappe.db.exists("Customer", {"customer_name": customer["nom"]}):
                continue
            doc = frappe.get_doc(
                {
                    "doctype": "Customer",
                    "customer_name": customer["nom"],
                    "customer_type": customer["type"],
                    "customer_group": self.leaf("Customer Group", customer["groupe"]),
                    "territory": territory,
                    "siren_number": customer.get("siren"),
                    "tax_id": customer.get("tva"),
                }
            ).insert()
            self.address(customer["nom"], "Customer", doc.name, customer["adresse"], customer.get("courriel"))
            if customer.get("courriel"):
                self.contact(customer["nom"], "Customer", doc.name, customer["courriel"])

        bank = self.data["banque"]
        if not frappe.db.exists("Bank", bank["nom"]):
            frappe.get_doc({"doctype": "Bank", "bank_name": bank["nom"], "swift_number": bank.get("bic")}).insert()
        for supplier in self.data["fournisseurs"]:
            if frappe.db.exists("Supplier", {"supplier_name": supplier["nom"]}):
                continue
            doc = frappe.get_doc(
                {
                    "doctype": "Supplier",
                    "supplier_name": supplier["nom"],
                    "supplier_type": "Company",
                    "supplier_group": self.leaf("Supplier Group", supplier["groupe"]),
                    "siren_number": supplier.get("siren"),
                    "tax_id": supplier.get("tva"),
                    "country": "France",
                }
            ).insert()
            self.address(supplier["nom"], "Supplier", doc.name, supplier["adresse"])
            if supplier.get("iban"):
                frappe.get_doc(
                    {
                        "doctype": "Bank Account",
                        "account_name": supplier["nom"],
                        "bank": bank["nom"],
                        "party_type": "Supplier",
                        "party": doc.name,
                        "iban": supplier["iban"],
                    }
                ).insert()

    def bank_account(self) -> tuple[str, str]:
        frappe = self.frappe
        bank = self.data["banque"]
        gl_account = self.account(bank["compte_comptable"])
        name = frappe.db.get_value("Bank Account", {"account_name": bank["compte"], "is_company_account": 1})
        if not name:
            name = frappe.get_doc(
                {
                    "doctype": "Bank Account",
                    "account_name": bank["compte"],
                    "bank": bank["nom"],
                    "is_company_account": 1,
                    "company": self.company,
                    "account": gl_account,
                    "iban": bank["iban"],
                    "is_default": 1,
                }
            ).insert().name
        # Dokos posts each Payment Entry in an accounting journal; the setup wizard only
        # creates the bank journal when it is given a bank account (company_setup.py,
        # create_accounting_journals), so the demo adds it the same way.
        if frappe.db.exists("DocType", "Accounting Journal") and not frappe.db.exists(
            "Accounting Journal", {"company": self.company, "account": gl_account}
        ):
            frappe.get_doc(
                {
                    "doctype": "Accounting Journal",
                    "journal_code": "BQ",
                    "journal_name": bank["nom"],
                    "type": "Bank",
                    "company": self.company,
                    "account": gl_account,
                    "conditions": [{"document_type": "Payment Entry"}],
                }
            ).insert()
        # Wire transfers default to this bank account, as an accountant would set it.
        if frappe.db.exists("Mode of Payment", "Wire Transfer"):
            mode = frappe.get_doc("Mode of Payment", "Wire Transfer")
            if not any(row.company == self.company for row in mode.accounts):
                mode.append("accounts", {"company": self.company, "default_account": gl_account})
                mode.save()
        return name, gl_account

    # ------------------------------------------------------------------ transactions

    def purchases(self, bank_account: str, gl_account: str) -> None:
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

        frappe = self.frappe
        warehouse = self.warehouse()
        tax = self.tax_template("Purchase Taxes and Charges Template", "TVA 20% Déductible")
        for order in self.data["commandes_achat"]:
            date = self.day(order["jour"])
            doc = frappe.get_doc(
                {
                    "doctype": "Purchase Order",
                    "supplier": order["fournisseur"],
                    "company": self.company,
                    "transaction_date": date,
                    "schedule_date": date + datetime.timedelta(days=7),
                    "set_warehouse": warehouse,
                    "items": [
                        {
                            "item_code": line["article"],
                            "qty": line["quantite"],
                            "rate": line["prix"],
                            "schedule_date": date + datetime.timedelta(days=7),
                            "warehouse": warehouse,
                        }
                        for line in order["lignes"]
                    ],
                }
            )
            self.with_taxes(doc, "Purchase Taxes and Charges Template", tax).insert()
            doc.submit()
            self.purchase_orders[order["cle"]] = doc.name
            receipt = order.get("reception")
            if receipt:
                pr = make_purchase_receipt(doc.name)
                pr.set_posting_time = 1
                pr.posting_date = self.day(receipt["jour"])
                pr.posting_time = "10:00:00"
                if receipt.get("lignes"):
                    wanted = {line["article"]: line["quantite"] for line in receipt["lignes"]}
                    pr.items = [row for row in pr.items if row.item_code in wanted]
                    for row in pr.items:
                        row.qty = row.received_qty = wanted[row.item_code]
                pr.insert()
                pr.submit()

        for invoice in self.data["factures_achat"]:
            date = self.day(invoice["jour"])
            doc = frappe.get_doc(
                {
                    "doctype": "Purchase Invoice",
                    "supplier": invoice["fournisseur"],
                    "company": self.company,
                    "set_posting_time": 1,
                    "posting_date": date,
                    "bill_no": invoice["numero_fournisseur"],
                    "bill_date": date,
                    "due_date": date + datetime.timedelta(days=30),
                    "items": [{"item_code": line["article"], "qty": line["quantite"], "rate": line["prix"]} for line in invoice["lignes"]],
                }
            )
            self.with_taxes(doc, "Purchase Taxes and Charges Template", tax).insert()
            doc.submit()
            if invoice.get("paiement"):
                self.payment("Purchase Invoice", doc.name, invoice["paiement"], bank_account, gl_account)

    def sales(self, bank_account: str, gl_account: str) -> None:
        frappe = self.frappe
        tax = self.tax_template("Sales Taxes and Charges Template", "TVA 20% Collectée")
        price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
        for invoice in self.data["factures_vente"]:
            date = self.day(invoice["jour"])
            doc = frappe.get_doc(
                {
                    "doctype": "Sales Invoice",
                    "customer": invoice["client"],
                    "company": self.company,
                    "set_posting_time": 1,
                    "posting_date": date,
                    "due_date": date + datetime.timedelta(days=30),
                    "selling_price_list": price_list,
                    "items": [
                        {"item_code": line["article"], "qty": line["quantite"], "rate": self.items[line["article"]]["vente"]}
                        for line in invoice["lignes"]
                    ],
                }
            )
            self.with_taxes(doc, "Sales Taxes and Charges Template", tax).insert()
            doc.submit()
            if invoice.get("paiement"):
                self.payment("Sales Invoice", doc.name, invoice["paiement"], bank_account, gl_account)

    def payment(self, doctype: str, name: str, payment: dict[str, Any], bank_account: str, gl_account: str) -> None:
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

        date = self.day(payment["jour"])
        entry = get_payment_entry(doctype, name, party_amount=payment.get("montant"), bank_account=gl_account)
        entry.posting_date = date
        entry.reference_no = payment["reference"]
        entry.reference_date = date
        entry.bank_account = bank_account
        if self.frappe.db.exists("Mode of Payment", "Wire Transfer"):
            entry.mode_of_payment = "Wire Transfer"
        entry.insert()
        entry.submit()

    def bank_transactions(self, bank_account: str) -> None:
        bank = self.data["banque"]
        reference = datetime.date.fromisoformat(str(bank["date_de_reference"]))
        shift = self.today - reference
        with open(HERE / bank["releve"], encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                doc = self.frappe.get_doc(
                    {
                        "doctype": "Bank Transaction",
                        "date": datetime.date.fromisoformat(row["date"]) + shift,
                        "bank_account": bank_account,
                        "company": self.company,
                        "currency": row.get("currency") or "EUR",
                        "deposit": float(row["deposit"] or 0),
                        "withdrawal": float(row["withdrawal"] or 0),
                        "description": row["description"],
                        "reference_number": row["reference_number"],
                    }
                )
                doc.insert()
                doc.submit()

    def received_einvoices(self) -> list[str]:
        """Generate each received e-invoice and import it through eTransactions."""
        frappe = self.frappe
        details = self.data["societe"]
        buyer = {
            "name": self.company,
            "siren": details["siren"],
            "vat": details["tva"],
            "address": {"line1": details["adresse"]["ligne1"], "postcode": details["adresse"]["code_postal"], "city": details["adresse"]["ville"]},
        }
        created = []
        for invoice in self.data["factures_recues"]:
            supplier = self.suppliers[invoice["fournisseur"]]
            issue = self.day(invoice["jour"])
            document = {
                "number": invoice["numero"],
                "issue_date": issue,
                "due_date": issue + datetime.timedelta(days=30),
                "syntax": invoice.get("syntaxe", "CII"),
                "order_reference": self.purchase_orders.get(invoice.get("commande", "")),
                "seller": {
                    "name": supplier["nom"],
                    "siren": supplier.get("siren"),
                    "vat": supplier.get("tva"),
                    "iban": supplier.get("iban"),
                    "address": {"line1": supplier["adresse"]["ligne1"], "postcode": supplier["adresse"]["code_postal"], "city": supplier["adresse"]["ville"]},
                },
                "buyer": buyer,
                "lines": [
                    {
                        "seller_id": line.get("ref_fournisseur"),
                        "buyer_id": line.get("article"),
                        "name": line.get("designation") or self.items[line["article"]]["nom"],
                        "quantity": line["quantite"],
                        "price": line["prix"],
                        "unit": "HUR" if line.get("article") and self.items[line["article"]]["unite"] == "Hour" else "C62",
                    }
                    for line in invoice["lignes"]
                ],
            }
            content = generate.render(document)
            file_doc = frappe.get_doc(
                {"doctype": "File", "file_name": f"{invoice['numero']}.xml", "content": content, "is_private": 1}
            ).insert()
            einvoice = frappe.get_doc({"doctype": "eInvoice", "einvoice_type": "Incoming", "einvoice": file_doc.name}).insert()
            supplier_invoice = frappe.db.get_value("Supplier Invoice", {"einvoice": einvoice.name})
            if not supplier_invoice:
                raise StepError(f"La facture reçue {invoice['numero']} n'a pas créé de facture fournisseur dans eTransactions.")
            created.append(supplier_invoice)
        return created

    def run(self) -> dict[str, Any]:
        self.company_details()
        self.fiscal_years()
        self.masters()
        bank_account, gl_account = self.bank_account()
        self.purchases(bank_account, gl_account)
        self.sales(bank_account, gl_account)
        self.bank_transactions(bank_account)
        return {"supplier_invoices": self.received_einvoices(), "purchase_orders": self.purchase_orders}


def counts(frappe: Any, company: str) -> dict[str, int]:
    submitted = {"company": company, "docstatus": 1}
    return {
        "customers": frappe.db.count("Customer"),
        "suppliers": frappe.db.count("Supplier"),
        "items": frappe.db.count("Item"),
        "purchase_orders": frappe.db.count("Purchase Order", submitted),
        "purchase_receipts": frappe.db.count("Purchase Receipt", submitted),
        "purchase_invoices": frappe.db.count("Purchase Invoice", submitted),
        "sales_invoices": frappe.db.count("Sales Invoice", submitted),
        "sales_invoices_overdue": frappe.db.count("Sales Invoice", {**submitted, "status": ["in", ["Overdue", "Partly Paid and Overdue"]]}),
        "sales_invoices_paid": frappe.db.count("Sales Invoice", {**submitted, "status": "Paid"}),
        "payment_entries": frappe.db.count("Payment Entry", submitted),
        "bank_transactions": frappe.db.count("Bank Transaction", submitted),
        "einvoices_received": frappe.db.count("eInvoice", {"einvoice_type": "Incoming"}),
        "supplier_invoices": frappe.db.count("Supplier Invoice", {"company": company}),
    }


def load(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, company. Loads the demo once; later runs change nothing."""
    from frappe.utils import getdate, nowdate

    site = payload["site"]
    require_site(site)
    data = yaml.safe_load(DATA_FILE.read_text(encoding="utf-8"))
    with site_connection(site) as frappe:
        if not frappe.db.exists("Company", payload["company"]):
            raise StepError(f"Société « {payload['company']} » introuvable : la configuration du site n'a pas abouti.")
        loaded_on = frappe.db.get_default(MARKER)
        if loaded_on:
            return {"changed": False, "loaded_on": loaded_on, "counts": counts(frappe, payload["company"])}
        today = getdate(nowdate())
        details = Loader(frappe, payload["company"], data, today).run()
        frappe.db.set_default(MARKER, str(today))
        frappe.db.commit()
        return {"changed": True, "loaded_on": str(today), "details": details, "counts": counts(frappe, payload["company"])}


def reset(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, db_root_password. Drops the demo site (database and files) so that
    the next installation run recreates it and loads fresh data. Refuses a site that
    was not loaded with the demo data."""
    import os

    import frappe
    from frappe.commands.site import _drop_site

    site = payload["site"]
    require_site(site)
    with site_connection(site) as connection:
        if not connection.db.get_default(MARKER):
            raise StepError(
                f"Le site « {site} » ne contient pas les données de test : DokoPilot refuse de le supprimer."
            )
    os.chdir(SITES_PATH)
    try:
        _drop_site(site, db_root_username="root", db_root_password=payload["db_root_password"], force=True, no_backup=True)
    except SystemExit as error:
        raise StepError(f"La suppression du site de démonstration a échoué (code {error.code}).") from None
    finally:
        frappe.destroy()
    return {"changed": True, "dropped": site}
