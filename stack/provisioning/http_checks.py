# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only HTTP checks of Dokos with each assistant's token, run on the host by check.yml.

    python3 http_checks.py < payload.json

Payload: base_url, assistants ([{user, api_key, api_secret}]), doctypes, forbidden_ptypes.
For each assistant: MCP `initialize` and `tools/list` on /api/method/dokos_ai.mcp.endpoint,
then, for each DocType that has at least one document the assistant can read,
`frappe.client.has_permission` for every forbidden permission type (expected: false).
Nothing is written. Prints one JSON object; tokens are never printed.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MCP_PATH = "/api/method/dokos_ai.mcp.endpoint"
PROTOCOL = "2025-11-25"


def call(url: str, token: str, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(url, method="POST" if body is not None else "GET")
    request.add_header("Authorization", f"token {token}")
    request.add_header("Accept", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    data = None
    if body is not None:
        request.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(request, data=data, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"null")
        except ValueError:
            return error.code, None


def check_assistant(base: str, assistant: dict[str, str], doctypes: list[str], ptypes: list[str]) -> dict[str, Any]:
    token = f"{assistant['api_key']}:{assistant['api_secret']}"
    result: dict[str, Any] = {"user": assistant["user"], "problems": []}
    status, body = call(
        base + MCP_PATH,
        token,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "dokopilot-check", "version": "1"}}},
    )
    result["initialize"] = status == 200 and "result" in (body or {})
    if not result["initialize"]:
        result["problems"].append(f"MCP initialize refusé (HTTP {status})")
        return result
    status, body = call(base + MCP_PATH, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, {"MCP-Protocol-Version": PROTOCOL})
    tools = [tool["name"] for tool in ((body or {}).get("result") or {}).get("tools", [])]
    result["tools"] = len(tools)
    if status != 200 or "doc_list" not in tools:
        result["problems"].append(f"MCP tools/list sans outil de lecture (HTTP {status})")

    checked: dict[str, Any] = {}
    for doctype in doctypes:
        query = urllib.parse.urlencode({"fields": '["name"]', "limit_page_length": 1})
        status, body = call(f"{base}/api/resource/{urllib.parse.quote(doctype)}?{query}", token)
        rows = (body or {}).get("data") if status == 200 else None
        if not rows:
            checked[doctype] = "aucun document lisible"
            continue
        name = rows[0]["name"]
        granted = []
        for ptype in ptypes:
            query = urllib.parse.urlencode({"doctype": doctype, "docname": name, "perm_type": ptype})
            status, body = call(f"{base}/api/method/frappe.client.has_permission?{query}", token)
            allowed = ((body or {}).get("message") or {}).get("has_permission") if status == 200 else None
            if allowed is not False:
                granted.append(f"{ptype} ({'oui' if allowed else f'HTTP {status}'})")
        checked[doctype] = granted or "ok"
        if granted:
            result["problems"].append(f"{doctype} {name} : {', '.join(granted)}")
    result["permissions"] = checked
    return result


def main() -> int:
    payload = json.loads(sys.stdin.read())
    base = payload["base_url"].rstrip("/")
    results = [check_assistant(base, a, payload["doctypes"], payload["forbidden_ptypes"]) for a in payload["assistants"]]
    json.dump({"ok": not any(r["problems"] for r in results), "assistants": results}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
