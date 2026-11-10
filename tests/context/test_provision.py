# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit checks of ansible/roles/forgejo/files/forgejo_provision.py that need no Forgejo.

Run by tests/context/run.sh before the Docker part:
    python -m unittest discover -s tests/context -p 'test_*.py'

- A login that Forgejo reserves or that collides with the administrator, the organisation
  or the assistants is refused with a French message BEFORE any call to Forgejo (the
  script is run against an address where nothing listens: the refusal must come first).
- The protection rule of the default branch keeps pushing open and has no protected file
  pattern (D-32).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[2] / "ansible/roles/forgejo/files/forgejo_provision.py"
SPEC = importlib.util.spec_from_file_location("forgejo_provision", SCRIPT)
assert SPEC and SPEC.loader
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)

# Nothing listens on the discard port: any call to Forgejo fails at once (« injoignable »).
NOWHERE = "http://127.0.0.1:9"


def person(identifier: str, role: str = "membre", assistant: bool = True) -> dict[str, Any]:
    return {"id": identifier, "email": f"{identifier}@exemple.test", "first_name": identifier.capitalize(),
            "last_name": "Test", "role": role, "assistant": assistant,
            "assistant_email": f"assistant.{identifier}@exemple.test"}


def payload(*people: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": NOWHERE,
        "compose": {"dir": "/nonexistent", "service": "forgejo"},
        "admin": {"user": "dokopilot-admin", "email": "dokopilot-admin@demo.dokopilot.internal", "password": "x" * 16},
        "org": {"name": "demo", "full_name": "Atelier Démo"},
        "people": [person("camille", "dirigeant"), *people],
        "direction": True,
        "default_branch": "main",
        "credentials_dir": "/nonexistent/forgejo",
        "password_length": 24,
        "token": {"name": "dokopilot", "scopes": ["write:repository"]},
        "app_ini": "/var/lib/gitea/custom/conf/app.ini",
        "settings": {
            "assistant_prefix": "assistant-", "assistant_full_name": "Assistant de {first_name}",
            "people_must_change_password": False, "assistants_restricted": True,
            "team_members": "membres", "team_direction": "direction",
            "team_units": ["repo.code"],
            "repo_descriptions": {"entreprise": "e", "direction": "d", "perso": "p {name}"},
        },
    }


def run_step(step: str, data: dict[str, Any]) -> tuple[int, dict[str, Any], float]:
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), step], input=json.dumps(data),
                            capture_output=True, text=True, timeout=60, check=False)
    return result.returncode, json.loads(result.stdout.strip().splitlines()[-1]), time.monotonic() - started


class LoginTest(unittest.TestCase):
    REFUSED = {
        "dokopilot-admin": "compte d'administration de Forgejo",
        "demo": "nom de l'organisation",
        "assistant-x": "réservé aux comptes des assistants",
        "admin": "nom réservé par Forgejo",
        "api": "nom réservé par Forgejo",
        "ghost": "nom réservé par Forgejo",
        "actor": "nom réservé par Forgejo",
        "forgejo-actions": "nom réservé par Forgejo",
        "Admin": "nom réservé par Forgejo",
        "lucas-": "caractères refusés par Forgejo",
        "lu--cas": "caractères refusés par Forgejo",
    }

    def test_refused_before_any_change(self) -> None:
        for identifier, reason in self.REFUSED.items():
            with self.subTest(identifier=identifier):
                with self.assertRaises(provision.Failure) as caught:
                    provision.complete(payload(person(identifier)))
                message = str(caught.exception)
                self.assertIn(reason, message)
                self.assertIn("Forgejo n'a pas été modifié", message)

    def test_every_problem_is_listed(self) -> None:
        with self.assertRaises(provision.Failure) as caught:
            provision.complete(payload(person("ghost", assistant=False), person("api")))
        self.assertIn("« ghost » est un nom réservé par Forgejo ; « api » est un nom réservé par Forgejo",
                      str(caught.exception))

    def test_valid_people_pass(self) -> None:
        model = provision.complete(payload(person("lucas"), person("marie-claire")))
        self.assertEqual([account["user"] for account in model["accounts"]],
                         ["camille", "assistant-camille", "lucas", "assistant-lucas", "marie-claire",
                          "assistant-marie-claire"])

    def test_ensure_refuses_without_calling_forgejo(self) -> None:
        code, result, seconds = run_step("ensure", payload(person("admin")))
        self.assertEqual(code, 1)
        self.assertTrue(result.get("failed"))
        self.assertIn("« admin » est un nom réservé par Forgejo", result["msg"])
        self.assertNotIn("injoignable", result["msg"])
        self.assertLess(seconds, 20, "the refusal must come before waiting for Forgejo")

    def test_check_refuses_too(self) -> None:
        code, result, _ = run_step("check", payload(person("assistant-lucas")))
        self.assertEqual(code, 1)
        self.assertIn("réservé aux comptes des assistants", result["msg"])

    def test_valid_payload_reaches_forgejo(self) -> None:
        # Control: with valid ids, the next thing the script does is calling Forgejo.
        code, result, _ = run_step("check", payload(person("lucas")))
        self.assertEqual(code, 1)
        self.assertIn("injoignable", result["msg"])


class ProtectionTest(unittest.TestCase):
    def test_pushing_stays_open_and_no_file_is_protected(self) -> None:
        self.assertIs(provision.PROTECTION["enable_push"], True)
        self.assertIs(provision.PROTECTION["enable_push_whitelist"], False)
        self.assertIs(provision.PROTECTION["require_signed_commits"], False)
        self.assertEqual(provision.PROTECTION["protected_file_patterns"], "")

    def test_differences(self) -> None:
        current = {**provision.PROTECTION, "rule_name": "main", "created_at": "x"}
        self.assertEqual(provision.protection_differences(current), [])
        self.assertEqual(provision.protection_differences({**current, "enable_push": False}), ["enable_push"])


if __name__ == "__main__":
    unittest.main()
