# SPDX-License-Identifier: AGPL-3.0-or-later
"""The custom role "Assistant IA": read everything useful, prepare drafts, validate nothing.

Rules are written with the Role Permission Manager API
(frappe.core.page.permission_manager.permission_manager.add / update), exactly what the
desk screen does. `add` first copies the standard rules of the DocType into Custom DocPerm
(frappe.permissions.setup_custom_perms), so that no standard role loses its access; a lone
Custom DocPerm row would replace every standard rule of that DocType
(frappe.model.meta.Meta.set_custom_permissions).

Whatever the payload asks, submit, cancel, amend and delete are never granted, and are
reset to 0 if someone ticked them in the desk.

Attached files: Dodock gives every user the right to delete a File attached to a document
they can write (role « All » on File, frappe/core/doctype/file/file.py, has_permission), even
once the document is submitted. An assistant, which writes drafts of invoices and payments,
could therefore delete the supplier's PDF of a validated invoice. The documented DocType
property « Protect Attached Files » (Customize Form) closes it: attachments of a submitted
document can no longer be deleted (File.validate_protected_file). It is set here, as Customize
Form does, on the document types listed in `protected_attachment_doctypes`.

Reports: a report has its own list of roles (Report.is_permitted), which the `report` right
on a DocType does not replace: without it, the MCP tool run_report refuses the general
ledger or the trial balance that the business skills use. The role is added to the reports
of `reports` as the desk screen « Role Permission for Page and Report » does: a Custom Role
that holds the report's standard roles plus this one (a Custom Role replaces the standard
list, so the standard roles are kept in it). Reading a report validates nothing.
"""

from __future__ import annotations

from typing import Any

from dokopilot.common import StepError, require_site, site_connection

FORBIDDEN_FLAGS = ("submit", "cancel", "amend", "delete")
# Flags this step manages on the role's rules; anything else keeps the desk's value.
MANAGED_FLAGS = (
    "read",
    "write",
    "create",
    "delete",
    "submit",
    "cancel",
    "amend",
    "report",
    "export",
    "import",
    "share",
    "print",
    "email",
    "select",
    "set_user_permissions",
)
AUTOMATIC_ROLES = ("Guest", "All", "Desk User", "Administrator")


def _rules(frappe: Any, doctype: str) -> list[Any]:
    rows = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields="*")
    return rows or frappe.get_all("DocPerm", filters={"parent": doctype}, fields="*")


def _readable_by_everyone(frappe: Any, doctype: str) -> bool:
    return any(
        row.role in AUTOMATIC_ROLES and not row.permlevel and row.read and not row.if_owner
        for row in _rules(frappe, doctype)
    )


def _field_levels(frappe: Any, doctype: str) -> list[int]:
    meta = frappe.get_meta(doctype)
    return sorted({int(field.permlevel or 0) for field in meta.fields if int(field.permlevel or 0) > 0})


def _ensure_rule(frappe: Any, doctype: str, role: str, permlevel: int, wanted: dict[str, int]) -> list[str]:
    from frappe.core.page.permission_manager import permission_manager

    meta = frappe.get_meta(doctype)
    available = {field.fieldname for field in frappe.get_meta("Custom DocPerm").fields}
    flags = {flag: int(wanted.get(flag, 0)) for flag in MANAGED_FLAGS if flag in available}
    if not meta.is_submittable:
        # These flags cannot be set on a DocType that is not submittable.
        for flag in ("submit", "cancel", "amend"):
            flags.pop(flag, None)

    changes: list[str] = []
    filters = {"parent": doctype, "role": role, "permlevel": permlevel, "if_owner": 0}
    if not frappe.db.exists("Custom DocPerm", filters):
        permission_manager.add(parent=doctype, role=role, permlevel=permlevel)
        changes.append(f"{doctype} (level {permlevel}): rule added")
    current = frappe.db.get_value("Custom DocPerm", filters, list(flags), as_dict=True) or {}
    # read first, so that the other flags never stand without it.
    for flag in sorted(flags, key=lambda name: name != "read"):
        if int(current.get(flag) or 0) != flags[flag]:
            permission_manager.update(
                doctype=doctype, role=role, permlevel=permlevel, ptype=flag, value=flags[flag]
            )
            changes.append(f"{doctype} (level {permlevel}): {flag}={flags[flag]}")
    return changes


def ensure_assistant_role(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, role, draft_doctypes, read_doctypes, draft_flags, read_flags.

    draft_doctypes get draft_flags (read plus create and write); read_doctypes get
    read_flags. Fields at a permission level above 0 are made readable, never writable.
    DocTypes that do not exist in this image are skipped and reported.
    """
    site = payload["site"]
    require_site(site)
    role = payload["role"]
    for flags in (payload["draft_flags"], payload["read_flags"]):
        granted = [flag for flag in FORBIDDEN_FLAGS if flags.get(flag)]
        if granted:
            raise StepError(f"Le rôle {role} ne peut pas recevoir les droits {', '.join(granted)}.")

    changes: list[str] = []
    skipped: list[str] = []
    already_readable: list[str] = []
    with site_connection(site) as frappe:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1, "is_custom": 1}).insert(
                ignore_permissions=True
            )
            changes.append(f"role {role} created")
        elif frappe.db.get_value("Role", role, "disabled"):
            frappe.db.set_value("Role", role, "disabled", 0)
            changes.append(f"role {role} enabled")

        plan: list[tuple[str, dict[str, int]]] = []
        for doctype in payload["draft_doctypes"]:
            plan.append((doctype, payload["draft_flags"]))
        for doctype in payload["read_doctypes"]:
            plan.append((doctype, payload["read_flags"]))

        for doctype, flags in plan:
            if not frappe.db.exists("DocType", doctype):
                skipped.append(doctype)
                continue
            if flags is payload["read_flags"] and _readable_by_everyone(frappe, doctype) and not frappe.db.exists(
                "Custom DocPerm", {"parent": doctype, "role": role}
            ):
                already_readable.append(doctype)
                continue
            changes += _ensure_rule(frappe, doctype, role, 0, flags)
            for level in _field_levels(frappe, doctype):
                changes += _ensure_rule(frappe, doctype, role, level, {"read": 1})

        # Rules for this role on any other DocType (added in the desk) lose the
        # forbidden flags too.
        for row in frappe.get_all("Custom DocPerm", filters={"role": role}, fields=["name", "parent", "permlevel", *FORBIDDEN_FLAGS]):
            for flag in FORBIDDEN_FLAGS:
                if row.get(flag):
                    from frappe.core.page.permission_manager import permission_manager

                    permission_manager.update(
                        doctype=row.parent, role=role, permlevel=row.permlevel, ptype=flag, value=0
                    )
                    changes.append(f"{row.parent} (level {row.permlevel}): {flag}=0")

        protected: list[str] = []
        for doctype in payload.get("protected_attachment_doctypes") or []:
            if not frappe.db.exists("DocType", doctype):
                continue
            if not frappe.get_meta(doctype).protect_attached_files:
                # The call Customize Form makes for a DocType property. The fields are not
                # validated again: the property does not touch them, and an upstream DocType
                # whose own fields fail that validation (Bank Transaction, a duplicate column
                # break in v5.18) would refuse any property.
                frappe.make_property_setter(
                    {
                        "doctype": doctype,
                        "doctype_or_field": "DocType",
                        "property": "protect_attached_files",
                        "value": 1,
                        "property_type": "Check",
                    },
                    validate_fields_for_doctype=False,
                    is_system_generated=False,
                )
                frappe.clear_cache(doctype=doctype)
                changes.append(f"{doctype}: attached files protected once submitted")
            protected.append(doctype)

        reports: list[str] = []
        for report in payload.get("reports") or []:
            if not frappe.db.exists("Report", report):
                continue
            custom = frappe.db.get_value("Custom Role", {"report": report}, "name")
            if custom:
                document = frappe.get_doc("Custom Role", custom)
                if role not in [row.role for row in document.roles]:
                    document.append("roles", {"role": role})
                    document.save(ignore_permissions=True)
                    changes.append(f"report {report}: role {role} added")
            else:
                standard = frappe.get_doc("Report", report)
                frappe.get_doc(
                    {
                        "doctype": "Custom Role",
                        "report": report,
                        "ref_doctype": standard.ref_doctype,
                        "roles": [{"role": name} for name in [*[row.role for row in standard.roles], role]],
                    }
                ).insert(ignore_permissions=True)
                changes.append(f"report {report}: role {role} added to its standard roles")
            reports.append(report)

        if changes:
            frappe.clear_cache()
        doctypes = sorted(frappe.get_all("Custom DocPerm", filters={"role": role}, pluck="parent", distinct=True))

    return {
        "changed": bool(changes),
        "changes": changes,
        "skipped": skipped,
        "readable_by_everyone": already_readable,
        "doctypes": doctypes,
        "protected_attachments": protected,
        "reports": reports,
    }
