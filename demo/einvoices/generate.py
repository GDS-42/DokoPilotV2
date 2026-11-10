#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generator of FICTITIOUS received e-invoices for the demo, following EN 16931.

Two syntaxes accepted by eTransactions (etransactions/utils/xml.py, detect_xml_format):
- UN/CEFACT Cross Industry Invoice (CII D16B), guideline urn:cen.eu:en16931:2017, the XML
  of a Factur-X EN 16931 invoice;
- OASIS UBL 2.1 Invoice, Peppol BIS Billing 3.0 (an EN 16931 compliant specification).

Only the standard library is used. The loader (demo/demo_loader.py) calls `render()` with
the real purchase order names and dates; run it alone to write sample files:

    python3 demo/einvoices/generate.py <output directory>
"""

from __future__ import annotations

import datetime
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

CII_NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}
UBL_NS = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
EN16931 = "urn:cen.eu:en16931:2017"
# UBL invoices are checked against the Peppol BIS Billing 3.0 rules by eTransactions.
PEPPOL_CUSTOMIZATION = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
PEPPOL_PROFILE = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
CENT = Decimal("0.01")
DEMO_NOTE = "Facture FICTIVE générée pour la démonstration DokoPilot : aucune valeur juridique ni fiscale."


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def totals(invoice: dict[str, Any]) -> dict[str, Any]:
    """Line totals and VAT breakdown (one rate per line, standard category S)."""
    lines = []
    by_rate: dict[Decimal, Decimal] = {}
    for line in invoice["lines"]:
        quantity = Decimal(str(line["quantity"]))
        price = money(line["price"])
        amount = money(quantity * price)
        rate = Decimal(str(line.get("vat_rate", 20)))
        lines.append({**line, "quantity": quantity, "price": price, "amount": amount, "vat_rate": rate})
        by_rate[rate] = by_rate.get(rate, Decimal(0)) + amount
    breakdown = [
        {"rate": rate, "basis": money(basis), "tax": money(basis * rate / 100)} for rate, basis in sorted(by_rate.items())
    ]
    net = money(sum((line["amount"] for line in lines), Decimal(0)))
    tax = money(sum((row["tax"] for row in breakdown), Decimal(0)))
    return {"lines": lines, "breakdown": breakdown, "net": net, "tax": tax, "gross": money(net + tax)}


def _sub(parent: ET.Element, tag: str, text: Any = None, **attributes: str) -> ET.Element:
    prefix, name = tag.split(":") if ":" in tag else ("", tag)
    namespaces = CII_NS if prefix in CII_NS else UBL_NS
    element = ET.SubElement(parent, f"{{{namespaces[prefix]}}}{name}", attributes)
    if text is not None:
        element.text = str(text)
    return element


def _cii_date(parent: ET.Element, tag: str, value: datetime.date) -> None:
    _sub(_sub(parent, tag), "udt:DateTimeString", value.strftime("%Y%m%d"), format="102")


def _cii_party(parent: ET.Element, tag: str, party: dict[str, Any]) -> None:
    node = _sub(parent, tag)
    _sub(node, "ram:Name", party["name"])
    if party.get("siren"):
        _sub(_sub(node, "ram:SpecifiedLegalOrganization"), "ram:ID", party["siren"], schemeID="0002")
    address = _sub(node, "ram:PostalTradeAddress")
    _sub(address, "ram:PostcodeCode", party["address"]["postcode"])
    _sub(address, "ram:LineOne", party["address"]["line1"])
    _sub(address, "ram:CityName", party["address"]["city"])
    _sub(address, "ram:CountryID", "FR")
    if party.get("siren"):
        # Electronic address (BT-34 / BT-49): SIREN, scheme 0225 of the French directory.
        _sub(_sub(node, "ram:URIUniversalCommunication"), "ram:URIID", party["siren"], schemeID="0225")
    if party.get("vat"):
        _sub(_sub(node, "ram:SpecifiedTaxRegistration"), "ram:ID", party["vat"], schemeID="VA")


def render_cii(invoice: dict[str, Any]) -> bytes:
    t = totals(invoice)
    for prefix, uri in CII_NS.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element(f"{{{CII_NS['rsm']}}}CrossIndustryInvoice")
    context = _sub(root, "rsm:ExchangedDocumentContext")
    _sub(_sub(context, "ram:GuidelineSpecifiedDocumentContextParameter"), "ram:ID", EN16931)
    header = _sub(root, "rsm:ExchangedDocument")
    _sub(header, "ram:ID", invoice["number"])
    _sub(header, "ram:TypeCode", "380")
    _cii_date(header, "ram:IssueDateTime", invoice["issue_date"])
    _sub(_sub(header, "ram:IncludedNote"), "ram:Content", DEMO_NOTE)

    transaction = _sub(root, "rsm:SupplyChainTradeTransaction")
    for index, line in enumerate(t["lines"], start=1):
        item = _sub(transaction, "ram:IncludedSupplyChainTradeLineItem")
        _sub(_sub(item, "ram:AssociatedDocumentLineDocument"), "ram:LineID", index)
        product = _sub(item, "ram:SpecifiedTradeProduct")
        if line.get("seller_id"):
            _sub(product, "ram:SellerAssignedID", line["seller_id"])
        if line.get("buyer_id"):
            _sub(product, "ram:BuyerAssignedID", line["buyer_id"])
        _sub(product, "ram:Name", line["name"])
        agreement = _sub(item, "ram:SpecifiedLineTradeAgreement")
        _sub(_sub(agreement, "ram:NetPriceProductTradePrice"), "ram:ChargeAmount", line["price"])
        _sub(_sub(item, "ram:SpecifiedLineTradeDelivery"), "ram:BilledQuantity", line["quantity"], unitCode=line.get("unit", "C62"))
        settlement = _sub(item, "ram:SpecifiedLineTradeSettlement")
        tax = _sub(settlement, "ram:ApplicableTradeTax")
        _sub(tax, "ram:TypeCode", "VAT")
        _sub(tax, "ram:CategoryCode", "S")
        _sub(tax, "ram:RateApplicablePercent", line["vat_rate"])
        _sub(_sub(settlement, "ram:SpecifiedTradeSettlementLineMonetarySummation"), "ram:LineTotalAmount", line["amount"])

    agreement = _sub(transaction, "ram:ApplicableHeaderTradeAgreement")
    if invoice.get("buyer_reference"):
        _sub(agreement, "ram:BuyerReference", invoice["buyer_reference"])
    _cii_party(agreement, "ram:SellerTradeParty", invoice["seller"])
    _cii_party(agreement, "ram:BuyerTradeParty", invoice["buyer"])
    if invoice.get("order_reference"):
        _sub(_sub(agreement, "ram:BuyerOrderReferencedDocument"), "ram:IssuerAssignedID", invoice["order_reference"])

    delivery = _sub(transaction, "ram:ApplicableHeaderTradeDelivery")
    _cii_date(_sub(delivery, "ram:ActualDeliverySupplyChainEvent"), "ram:OccurrenceDateTime", invoice.get("delivery_date", invoice["issue_date"]))

    settlement = _sub(transaction, "ram:ApplicableHeaderTradeSettlement")
    _sub(settlement, "ram:InvoiceCurrencyCode", "EUR")
    means = _sub(settlement, "ram:SpecifiedTradeSettlementPaymentMeans")
    _sub(means, "ram:TypeCode", "58")
    if invoice["seller"].get("iban"):
        _sub(_sub(means, "ram:PayeePartyCreditorFinancialAccount"), "ram:IBANID", invoice["seller"]["iban"])
    for row in t["breakdown"]:
        tax = _sub(settlement, "ram:ApplicableTradeTax")
        _sub(tax, "ram:CalculatedAmount", row["tax"])
        _sub(tax, "ram:TypeCode", "VAT")
        _sub(tax, "ram:BasisAmount", row["basis"])
        _sub(tax, "ram:CategoryCode", "S")
        _sub(tax, "ram:RateApplicablePercent", row["rate"])
    terms = _sub(settlement, "ram:SpecifiedTradePaymentTerms")
    _sub(terms, "ram:Description", "Paiement à 30 jours par virement")
    _cii_date(terms, "ram:DueDateDateTime", invoice["due_date"])
    summation = _sub(settlement, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _sub(summation, "ram:LineTotalAmount", t["net"])
    _sub(summation, "ram:TaxBasisTotalAmount", t["net"])
    _sub(summation, "ram:TaxTotalAmount", t["tax"], currencyID="EUR")
    _sub(summation, "ram:GrandTotalAmount", t["gross"])
    _sub(summation, "ram:DuePayableAmount", t["gross"])
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _ubl_party(parent: ET.Element, tag: str, party: dict[str, Any]) -> None:
    node = _sub(_sub(parent, tag), "cac:Party")
    if party.get("siren"):
        _sub(node, "cbc:EndpointID", party["siren"], schemeID="0225")
    _sub(_sub(node, "cac:PartyName"), "cbc:Name", party["name"])
    address = _sub(node, "cac:PostalAddress")
    _sub(address, "cbc:StreetName", party["address"]["line1"])
    _sub(address, "cbc:CityName", party["address"]["city"])
    _sub(address, "cbc:PostalZone", party["address"]["postcode"])
    _sub(_sub(address, "cac:Country"), "cbc:IdentificationCode", "FR")
    if party.get("vat"):
        scheme = _sub(node, "cac:PartyTaxScheme")
        _sub(scheme, "cbc:CompanyID", party["vat"])
        _sub(_sub(scheme, "cac:TaxScheme"), "cbc:ID", "VAT")
    legal = _sub(node, "cac:PartyLegalEntity")
    _sub(legal, "cbc:RegistrationName", party["name"])
    if party.get("siren"):
        _sub(legal, "cbc:CompanyID", party["siren"], schemeID="0002")


def render_ubl(invoice: dict[str, Any]) -> bytes:
    t = totals(invoice)
    for prefix, uri in UBL_NS.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element(f"{{{UBL_NS['']}}}Invoice")
    _sub(root, "cbc:CustomizationID", PEPPOL_CUSTOMIZATION)
    _sub(root, "cbc:ProfileID", PEPPOL_PROFILE)
    _sub(root, "cbc:ID", invoice["number"])
    _sub(root, "cbc:IssueDate", invoice["issue_date"].isoformat())
    _sub(root, "cbc:DueDate", invoice["due_date"].isoformat())
    _sub(root, "cbc:InvoiceTypeCode", "380")
    _sub(root, "cbc:Note", DEMO_NOTE)
    _sub(root, "cbc:DocumentCurrencyCode", "EUR")
    _sub(root, "cbc:BuyerReference", invoice.get("buyer_reference") or invoice["buyer"]["name"])
    if invoice.get("order_reference"):
        _sub(_sub(root, "cac:OrderReference"), "cbc:ID", invoice["order_reference"])
    _ubl_party(root, "cac:AccountingSupplierParty", invoice["seller"])
    _ubl_party(root, "cac:AccountingCustomerParty", invoice["buyer"])
    means = _sub(root, "cac:PaymentMeans")
    _sub(means, "cbc:PaymentMeansCode", "58")
    if invoice["seller"].get("iban"):
        _sub(_sub(means, "cac:PayeeFinancialAccount"), "cbc:ID", invoice["seller"]["iban"])
    _sub(_sub(root, "cac:PaymentTerms"), "cbc:Note", "Paiement à 30 jours par virement")
    tax_total = _sub(root, "cac:TaxTotal")
    _sub(tax_total, "cbc:TaxAmount", t["tax"], currencyID="EUR")
    for row in t["breakdown"]:
        subtotal = _sub(tax_total, "cac:TaxSubtotal")
        _sub(subtotal, "cbc:TaxableAmount", row["basis"], currencyID="EUR")
        _sub(subtotal, "cbc:TaxAmount", row["tax"], currencyID="EUR")
        category = _sub(subtotal, "cac:TaxCategory")
        _sub(category, "cbc:ID", "S")
        _sub(category, "cbc:Percent", row["rate"])
        _sub(_sub(category, "cac:TaxScheme"), "cbc:ID", "VAT")
    monetary = _sub(root, "cac:LegalMonetaryTotal")
    _sub(monetary, "cbc:LineExtensionAmount", t["net"], currencyID="EUR")
    _sub(monetary, "cbc:TaxExclusiveAmount", t["net"], currencyID="EUR")
    _sub(monetary, "cbc:TaxInclusiveAmount", t["gross"], currencyID="EUR")
    _sub(monetary, "cbc:PayableAmount", t["gross"], currencyID="EUR")
    for index, line in enumerate(t["lines"], start=1):
        node = _sub(root, "cac:InvoiceLine")
        _sub(node, "cbc:ID", index)
        _sub(node, "cbc:InvoicedQuantity", line["quantity"], unitCode=line.get("unit", "C62"))
        _sub(node, "cbc:LineExtensionAmount", line["amount"], currencyID="EUR")
        item = _sub(node, "cac:Item")
        _sub(item, "cbc:Name", line["name"])
        if line.get("buyer_id"):
            _sub(_sub(item, "cac:BuyersItemIdentification"), "cbc:ID", line["buyer_id"])
        if line.get("seller_id"):
            _sub(_sub(item, "cac:SellersItemIdentification"), "cbc:ID", line["seller_id"])
        category = _sub(item, "cac:ClassifiedTaxCategory")
        _sub(category, "cbc:ID", "S")
        _sub(category, "cbc:Percent", line["vat_rate"])
        _sub(_sub(category, "cac:TaxScheme"), "cbc:ID", "VAT")
        _sub(_sub(node, "cac:Price"), "cbc:PriceAmount", line["price"], currencyID="EUR")
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render(invoice: dict[str, Any]) -> bytes:
    """invoice: number, issue_date, due_date (dates), syntax (CII|UBL), seller, buyer
    ({name, siren, vat, iban, address: {line1, postcode, city}}), order_reference,
    lines ([{seller_id, buyer_id, name, quantity, price, vat_rate, unit}])."""
    return render_ubl(invoice) if invoice.get("syntax", "CII").upper() == "UBL" else render_cii(invoice)


def sample_invoices(today: datetime.date) -> list[dict[str, Any]]:
    """Two stand-alone samples (no purchase order), for a manual import test."""
    buyer = {
        "name": "Atelier Démo",
        "siren": "000000018",
        "vat": "FR66000000018",
        "address": {"line1": "12 rue des Copeaux", "postcode": "39000", "city": "Lons-le-Saunier"},
    }
    seller = {
        "name": "Transports Démo Express",
        "siren": "000002055",
        "vat": "FR66000002055",
        "iban": "FR7699999000010000000020582",
        "address": {"line1": "9 route de Lyon", "postcode": "39000", "city": "Lons-le-Saunier"},
    }
    base = {"issue_date": today, "due_date": today + datetime.timedelta(days=30), "seller": seller, "buyer": buyer}
    line = {"seller_id": "TRANSP-REG", "name": "Transport de bois (exemple)", "quantity": 1, "price": 150}
    return [
        {**base, "number": "EXEMPLE-CII-1", "syntax": "CII", "lines": [line]},
        {**base, "number": "EXEMPLE-UBL-1", "syntax": "UBL", "lines": [line]},
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: generate.py <output directory>\n")
        return 2
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for invoice in sample_invoices(datetime.date.today()):
        path = out / f"{invoice['number']}.xml"
        path.write_bytes(render(invoice))
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
