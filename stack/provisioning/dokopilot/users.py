# SPDX-License-Identifier: AGPL-3.0-or-later
"""The entrepreneur's user and the assistant users (one per person using an agent).

Assistants hold exactly the roles given in the payload (`Assistant IA` and `AI Writer`):
Frappe permissions add up across roles, so any business role would bring submit or
cancel rights with it. Extra roles found on an assistant are removed. API keys are
generated only when absent; the current key and secret are returned so that Ansible can
write them to the server's credential files.
"""

from __future__ import annotations

from typing import Any

from dokopilot.common import StepError, require_site, site_connection

# Roles an assistant must never hold (rule 3 of CLAUDE.md).
FORBIDDEN_ASSISTANT_ROLES = ("Administrator", "System Manager", "AI Manager")


def _existing_roles(frappe: Any, roles: list[str]) -> list[str]:
    return [role for role in roles if frappe.db.exists("Role", role)]


def ensure_entrepreneur(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, email, first_name, last_name, password, language, roles.

    The password is only set when the user is created: it is an initial password that
    the entrepreneur changes; later runs never reset it.
    """
    site = payload["site"]
    require_site(site)
    changes: list[str] = []
    with site_connection(site) as frappe:
        wanted = _existing_roles(frappe, payload["roles"])
        email = payload["email"]
        if not frappe.db.exists("User", email):
            user = frappe.new_doc("User")
            user.update(
                {
                    "email": email,
                    "first_name": payload["first_name"],
                    "last_name": payload.get("last_name") or "",
                    "language": payload.get("language") or "fr",
                    "send_welcome_email": 0,
                    "new_password": payload["password"],
                }
            )
            for role in wanted:
                user.append("roles", {"role": role})
            user.flags.ignore_permissions = True
            user.insert()
            changes.append("created")
        else:
            user = frappe.get_doc("User", email)
            missing = [role for role in wanted if role not in {row.role for row in user.roles}]
            if missing:
                user.flags.ignore_permissions = True
                user.add_roles(*missing)
                changes.append(f"roles added: {', '.join(missing)}")
        roles = sorted(frappe.get_roles(email))
    return {"changed": bool(changes), "changes": changes, "user": email, "roles": roles}


def _ensure_keys(frappe: Any, user: Any, previous: dict[str, str] | None) -> tuple[str, str, bool]:
    """Keep the user's API key; create one only when it has none.

    `previous` holds the credentials already written on the server for this user (after
    a demo reset, the site is new but the harness keeps working with the same token).
    """
    from frappe.utils.password import get_decrypted_password

    changed = False
    if not user.api_key:
        reusable = (
            previous
            and previous.get("api_key")
            and previous.get("api_secret")
            and not frappe.db.exists("User", {"api_key": previous["api_key"]})
        )
        user.api_key = previous["api_key"] if reusable else frappe.generate_hash(length=15)
        user.api_secret = previous["api_secret"] if reusable else frappe.generate_hash(length=15)
        changed = True
    elif not get_decrypted_password("User", user.name, "api_secret", raise_exception=False):
        user.api_secret = frappe.generate_hash(length=15)
        changed = True
    if changed:
        user.flags.ignore_permissions = True
        user.save()
    secret = get_decrypted_password("User", user.name, "api_secret", raise_exception=False)
    if not secret:
        raise StepError(f"Impossible de lire le jeton d'API de {user.name}.")
    return user.api_key, secret, changed


def ensure_assistants(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, roles, marker_role, language, assistants: [{email, for}],
    people: {email: full name} (the person each assistant works for),
    existing_credentials: [{user, api_key, api_secret}] already written on the server.

    Returns the credentials of every assistant ({user, api_key, api_secret}). Users that
    hold `marker_role` (Assistant IA) but are no longer configured are disabled.
    """
    site = payload["site"]
    require_site(site)
    wanted_roles = list(payload["roles"])
    if set(wanted_roles) & set(FORBIDDEN_ASSISTANT_ROLES):
        raise StepError("Un assistant ne peut pas recevoir les rôles Administrator, System Manager ou AI Manager.")
    changes: list[str] = []
    credentials: list[dict[str, str]] = []
    with site_connection(site) as frappe:
        missing_roles = [role for role in wanted_roles if not frappe.db.exists("Role", role)]
        if missing_roles:
            raise StepError(f"Rôles introuvables dans Dokos : {', '.join(missing_roles)}.")
        for assistant in payload["assistants"]:
            email = assistant["email"]
            if not frappe.db.exists("User", email):
                user = frappe.new_doc("User")
                user.update(
                    {
                        "email": email,
                        "first_name": "Assistant",
                        "last_name": (payload.get("people") or {}).get(assistant.get("for"))
                        or assistant.get("for")
                        or "",
                        "language": payload.get("language") or "fr",
                        "send_welcome_email": 0,
                        "user_type": "System User",
                    }
                )
                for role in wanted_roles:
                    user.append("roles", {"role": role})
                user.flags.ignore_permissions = True
                user.insert()
                changes.append(f"{email}: created")
            user = frappe.get_doc("User", email)
            current = sorted({row.role for row in user.roles})
            if current != sorted(wanted_roles):
                user.set("roles", [])
                for role in wanted_roles:
                    user.append("roles", {"role": role})
                user.flags.ignore_permissions = True
                user.save()
                changes.append(f"{email}: roles reset (were {', '.join(current) or 'none'})")
                user = frappe.get_doc("User", email)
            previous = next(
                (row for row in payload.get("existing_credentials") or [] if row.get("user") == email), None
            )
            api_key, api_secret, keys_changed = _ensure_keys(frappe, user, previous)
            if keys_changed:
                changes.append(f"{email}: API key generated")
            held = set(frappe.get_roles(email))
            if held & set(FORBIDDEN_ASSISTANT_ROLES):
                raise StepError(f"L'assistant {email} détient un rôle interdit : {', '.join(held & set(FORBIDDEN_ASSISTANT_ROLES))}.")
            credentials.append({"user": email, "api_key": api_key, "api_secret": api_secret})

        # An assistant removed from the configuration loses its access.
        configured = {assistant["email"] for assistant in payload["assistants"]}
        disabled: list[str] = []
        holders = frappe.get_all("Has Role", filters={"role": payload["marker_role"], "parenttype": "User"}, pluck="parent")
        for email in sorted(set(holders) - configured):
            if frappe.db.get_value("User", email, "enabled"):
                user = frappe.get_doc("User", email)
                user.enabled = 0
                user.flags.ignore_permissions = True
                user.save()
                changes.append(f"{email}: disabled (no longer configured)")
            disabled.append(email)
    return {"changed": bool(changes), "changes": changes, "credentials": credentials, "disabled": disabled}
