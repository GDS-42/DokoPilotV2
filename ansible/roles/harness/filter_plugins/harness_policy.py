# SPDX-License-Identifier: AGPL-3.0-or-later
"""Jinja filters that build the opencode permission rules of the DokoPilot harness.

``dokopilot_rules`` turns tool names into ordered opencode V2 rules
(``{"action", "resource", "effect"}``, last matching rule wins).

``dokopilot_opencode_v1`` converts ordered V2 rules into the OpenCode 1 ``permission``
object. OpenCode 1 groups patterns by permission key and evaluates the flattened object,
last match wins (packages/opencode/src/permission/index.ts at v1.19.5), so grouping can
change a decision when patterns of different keys overlap. The filter therefore evaluates
both forms on probes built from the rules and refuses a conversion that changes any
decision.
"""

from __future__ import annotations

import re
from typing import Any

from ansible.errors import AnsibleFilterError

EFFECTS = ("allow", "ask", "deny")
# V2 actions spelled differently in V1 (opencode packages/core/src/v1/config/migrate.ts).
V1_ACTION_NAMES = {"shell": "bash", "subagent": "task"}
# V2 actions that V1 does not have (Code Mode).
V1_DROPPED_ACTIONS = {"execute"}
# V1 permission keys that take a single effect, not an object of patterns.
V1_SCALAR_KEYS = {"webfetch", "websearch", "question", "doom_loop"}

Rule = dict[str, str]


def dokopilot_rules(names: Any, effect: str, prefix: str = "", resource: str = "*") -> list[Rule]:
    """Return one rule per tool name, in order."""
    if effect not in EFFECTS:
        raise AnsibleFilterError(f"dokopilot_rules: unknown effect {effect!r}")
    if isinstance(names, str):
        names = [names]
    return [{"action": f"{prefix}{name}", "resource": resource, "effect": effect} for name in names]


def _pattern(pattern: str) -> re.Pattern[str]:
    """Compile an opencode wildcard: `*` any run (slash included), `?` one character.

    A pattern ending in " *" also matches the bare command (opencode util/wildcard.ts).
    """

    def convert(text: str) -> str:
        return "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in text)

    if pattern.endswith(" *"):
        return re.compile(convert(pattern[:-2]) + "(?: .*)?", re.DOTALL)
    return re.compile(convert(pattern), re.DOTALL)


def _evaluate(rules: list[tuple[re.Pattern[str], re.Pattern[str], str]], action: str, resource: str) -> str | None:
    """Effect of the last rule matching both values, or None when no rule matches."""
    for action_re, resource_re, effect in reversed(rules):
        if action_re.fullmatch(action) and resource_re.fullmatch(resource):
            return effect
    return None


def _compile_v2(rules: list[Rule]) -> list[tuple[re.Pattern[str], re.Pattern[str], str]]:
    return [
        (_pattern(V1_ACTION_NAMES.get(r["action"], r["action"])), _pattern(r["resource"]), r["effect"])
        for r in rules
        if r["action"] not in V1_DROPPED_ACTIONS
    ]


def _compile_v1(permission: dict[str, Any]) -> list[tuple[re.Pattern[str], re.Pattern[str], str]]:
    compiled = []
    for key, value in permission.items():
        patterns = {"*": value} if isinstance(value, str) else value
        for resource, effect in patterns.items():
            compiled.append((_pattern(key), _pattern(resource), effect))
    return compiled


def _check(rules: list[Rule], base: list[Rule], v1_rules: dict[str, Any], v1_base: dict[str, Any]) -> None:
    every = [r for r in base + rules if r["action"] not in V1_DROPPED_ACTIONS]
    actions = {V1_ACTION_NAMES.get(r["action"], r["action"]).replace("*", "x").replace("?", "x") for r in every}
    resources = {r["resource"].replace("*", "x").replace("?", "x") for r in every} | {"x", ""}
    expected = _compile_v2(base + rules)
    converted = _compile_v1(v1_base) + _compile_v1(v1_rules)
    mismatches = [
        f"{action} {resource!r}: V2 {_evaluate(expected, action, resource)}, V1 {_evaluate(converted, action, resource)}"
        for action in sorted(actions)
        for resource in sorted(resources)
        if _evaluate(expected, action, resource) != _evaluate(converted, action, resource)
    ]
    if mismatches:
        raise AnsibleFilterError("dokopilot_opencode_v1: the V1 form would change these decisions: " + "; ".join(mismatches[:10]))


def _convert(rules: list[Rule]) -> dict[str, Any]:
    grouped: dict[str, dict[str, str]] = {}
    for rule in rules:
        if rule["action"] in V1_DROPPED_ACTIONS:
            continue
        key = V1_ACTION_NAMES.get(rule["action"], rule["action"])
        patterns = grouped.setdefault(key, {})
        # A repeated pattern moves to the end of its key, because the later rule wins.
        patterns.pop(rule["resource"], None)
        patterns[rule["resource"]] = rule["effect"]
    permission: dict[str, Any] = {}
    for key, patterns in grouped.items():
        if list(patterns) == ["*"]:
            permission[key] = patterns["*"]
        elif key in V1_SCALAR_KEYS:
            raise AnsibleFilterError(f"dokopilot_opencode_v1: OpenCode 1 accepts no pattern for {key!r}")
        else:
            permission[key] = patterns
    return permission


def dokopilot_opencode_v1(rules: list[Rule], base: list[Rule] | None = None) -> dict[str, Any]:
    """Convert V2 rules to the V1 `permission` object, checked against the V2 meaning.

    `base` holds the rules that OpenCode applies before these ones (the global rules, for an
    agent): only `rules` is converted, the check covers the concatenation.
    """
    base = list(base or [])
    for rule in base + list(rules):
        if not {"action", "resource", "effect"} <= set(rule) or rule["effect"] not in EFFECTS:
            raise AnsibleFilterError(f"dokopilot_opencode_v1: invalid rule {rule!r}")
    converted = _convert(list(rules))
    _check(list(rules), base, converted, _convert(base) if base else {})
    return converted


class FilterModule:
    """Filters of the harness role."""

    def filters(self) -> dict[str, Any]:
        return {
            "dokopilot_rules": dokopilot_rules,
            "dokopilot_opencode_v1": dokopilot_opencode_v1,
        }
