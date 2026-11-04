# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dokos AI settings: MCP server, optional model providers, pipeline templates.

The MCP server (`/api/method/dokos_ai.mcp.endpoint`) only depends on
`AI Settings.enable_mcp_server` (dokos_ai/dokos_ai/mcp_server/protocol.py, is_enabled) and
on the caller's AI capability role; it calls no model, so it works without any provider
and with `enable_ai` off. Providers are only needed by Dokos AI's own assistant and
pipelines inside Dokos.

Pipeline templates are instantiated with the app's own API
(dokos_ai.dokos_ai.api.pipeline_templates.create_pipeline_from_template), which creates
them disabled, with a Manual trigger.
"""

from __future__ import annotations

from typing import Any

from dokopilot.common import require_site, site_connection

TIER_FIELDS = {"DEFAULT": "default_model", "FAST": "fast_model", "VISION": "ocr_model"}


def _connect_provider(frappe: Any, entry: dict[str, Any]) -> tuple[str, bool]:
    """Connect one provider unless it is already enabled with the same key."""
    from dokos_ai.dokos_ai.api.providers import connect_provider

    provider = entry["provider"]
    if frappe.db.exists("AI Provider Connection", provider):
        connection = frappe.get_doc("AI Provider Connection", provider)
        same_key = (connection.get_password("api_key", raise_exception=False) or "") == entry["api_key"]
        if connection.enabled and same_key and connection.get("status") != "Error":
            return "unchanged", False
    result = connect_provider(provider=provider, api_key=entry["api_key"], api_base=entry.get("api_base"))
    return ("connected" if result.get("ok") else f"connection test failed: {result.get('message') or result.get('error') or ''}"), True


def _tier_values(frappe: Any, entry: dict[str, Any]) -> dict[str, str]:
    """AI Model names for the tiers: the entry's own, else the provider's defaults."""
    from dokos_ai.dokos_ai.providers import get_spec

    spec = get_spec(entry["provider"])
    values: dict[str, str] = {}
    for tier, model in (spec.default_models or {}).items():
        field = TIER_FIELDS.get(getattr(tier, "name", str(tier)))
        if field:
            values[field] = model
    for field in TIER_FIELDS.values():
        if entry.get(field):
            values[field] = entry[field]
    return {field: model for field, model in values.items() if frappe.db.exists("AI Model", model)}


def provider_entries(providers: list[Any], keys: dict[str, Any]) -> list[dict[str, Any]]:
    """Providers of the client settings (names or dicts) that have a key in the secrets."""
    entries = []
    for item in providers or []:
        entry = {"provider": item} if isinstance(item, str) else dict(item)
        key = (keys or {}).get(entry.get("provider", ""))
        if isinstance(key, dict):
            entry.update({k: v for k, v in key.items() if k in ("api_key", "api_base")})
        elif key:
            entry["api_key"] = key
        if entry.get("provider") and entry.get("api_key"):
            entries.append(entry)
    return entries


def configure(payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: site, mcp_allow_writes, providers (client settings: names, or dicts with
    provider, api_base?, default_model?, fast_model?, ocr_model?), provider_keys (secrets:
    {provider: key}), pipeline_templates (template ids). A provider without a key is
    ignored."""
    site = payload["site"]
    require_site(site)
    changes: list[str] = []
    providers_status: dict[str, str] = {}
    with site_connection(site) as frappe:
        from dokos_ai.dokos_ai.api.pipeline_templates import create_pipeline_from_template

        wanted: dict[str, Any] = {
            "enable_mcp_server": 1,
            "mcp_allow_writes": 1 if payload.get("mcp_allow_writes") else 0,
        }

        connected = []
        for entry in provider_entries(payload.get("providers"), payload.get("provider_keys")):
            status, changed = _connect_provider(frappe, entry)
            providers_status[entry["provider"]] = status
            if changed:
                changes.append(f"provider {entry['provider']}: {status}")
            if status in ("connected", "unchanged"):
                connected.append(entry)
        if connected:
            # The first provider listed sets the tiers; models synced in the background
            # appear on a later run if they are not there yet.
            wanted.update(_tier_values(frappe, connected[0]))
            wanted["enable_ai"] = 1

        settings = frappe.get_single("AI Settings")
        differing = {field: value for field, value in wanted.items() if settings.get(field) != value}
        if differing:
            settings.update(differing)
            settings.flags.ignore_permissions = True
            settings.save()
            changes.append(f"AI Settings: {', '.join(sorted(differing))}")

        pipelines: dict[str, list[str]] = {}
        for template_id in payload.get("pipeline_templates") or []:
            existing = frappe.get_all("AI Pipeline", filters={"template_id": template_id}, pluck="name")
            if not existing:
                create_pipeline_from_template(template_id)
                changes.append(f"pipeline template {template_id} created (disabled)")
                existing = frappe.get_all("AI Pipeline", filters={"template_id": template_id}, pluck="name")
            pipelines[template_id] = existing

        settings = frappe.get_single("AI Settings")
        state = {
            "enable_mcp_server": settings.enable_mcp_server,
            "mcp_allow_writes": settings.mcp_allow_writes,
            "enable_ai": settings.enable_ai,
        }
        enabled_pipelines = frappe.get_all("AI Pipeline", filters={"enabled": 1}, pluck="name")

    return {
        "changed": bool(changes),
        "changes": changes,
        "settings": state,
        "providers": providers_status,
        "pipelines": pipelines,
        "enabled_pipelines": enabled_pipelines,
    }
