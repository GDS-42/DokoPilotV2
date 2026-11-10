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


class FakeApi:
    """Records the calls of the provisioning functions; answers from canned users and tokens."""

    def __init__(self, users: dict[str, dict[str, Any]], tokens: dict[str, list[dict[str, Any]]] | None = None,
                 refuse: tuple[str, ...] = ()) -> None:
        self.users, self.tokens, self.refuse = users, tokens or {}, refuse
        self.calls: list[tuple[str, str, Any]] = []

    def get(self, path: str, missing_ok: bool = False) -> Any:
        return self.users.get(path.removeprefix("/users/"))

    def paged(self, path: str) -> list[Any]:
        if "paged" in self.refuse:
            raise provision.Failure("Forgejo a refusé GET (401)")
        return list(self.tokens.get(path.split("/")[2], []))

    def call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200,), **_: Any) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        if method in self.refuse:
            raise provision.Failure(f"Forgejo a refusé {method} {path} (500)")
        return 200, {"login": "dokopilot-admin", "is_admin": True}


class RemovedAndPromotedAccountsTest(unittest.TestCase):
    def model(self, *people: dict[str, Any]) -> dict[str, Any]:
        return provision.complete(payload(*people))

    def test_a_removed_account_loses_every_token_and_its_password(self) -> None:
        api = FakeApi({"marie": {"login": "marie", "prohibit_login": False}},
                      {"marie": [{"id": 7, "name": "dokopilot"}, {"id": 8, "name": "créé dans l'interface web"}]})
        run = provision.Run()
        model = self.model()
        self.assertEqual(provision.disable_removed(model, api, run, {"marie"}), ["marie"])
        patches = [body for method, path, body in api.calls if method == "PATCH" and path == "/admin/users/marie"]
        self.assertEqual(len(patches), 1)
        self.assertTrue(patches[0]["prohibit_login"])
        # A random password nobody keeps: the one the person knew no longer opens anything.
        self.assertGreaterEqual(len(patches[0]["password"]), model["password_length"])
        deleted = sorted(path for method, path, _ in api.calls if method == "DELETE")
        self.assertEqual(deleted, ["/users/marie/tokens/7", "/users/marie/tokens/8"])

    def test_a_configured_account_is_never_a_site_administrator(self) -> None:
        model = self.model(person("lucas"))
        account = next(a for a in model["accounts"] if a["user"] == "lucas")
        current = {"email": account["email"], "full_name": account["full_name"], "restricted": account["restricted"],
                   "prohibit_login": False, "active": True, "is_admin": True}
        users = {a["user"]: {"email": a["email"], "full_name": a["full_name"], "restricted": a["restricted"],
                             "prohibit_login": False, "active": True} for a in model["accounts"]}
        users["lucas"] = current
        api, run = FakeApi(users), provision.Run()
        provision.ensure_accounts(model, api, run)
        self.assertEqual([(m, p, b["admin"]) for m, p, b in api.calls], [("PATCH", "/admin/users/lucas", False)])
        self.assertIn("droits d'administration de Forgejo retirés", run.changes[0])

    def test_a_reactivated_account_is_pointed_out(self) -> None:
        model = self.model(person("marie"))
        users = {a["user"]: {"email": a["email"], "full_name": a["full_name"], "restricted": a["restricted"],
                             "prohibit_login": a["user"] == "marie", "active": True} for a in model["accounts"]}
        api, run = FakeApi(users), provision.Run()
        provision.ensure_accounts(model, api, run)
        self.assertEqual(len(run.changes), 1)
        self.assertIn("RÉACTIVÉ", run.changes[0])
        self.assertIn("choisissez un autre id", run.changes[0])

    def test_the_organisation_name_is_not_a_reserved_name(self) -> None:
        reserved = payload()
        reserved["org"]["name"] = "api"
        with self.assertRaises(provision.Failure) as caught:
            provision.complete(reserved)
        self.assertIn("ne peut pas nommer l'organisation", str(caught.exception))


class AdminBootstrapTest(unittest.TestCase):
    """The administration token minted to set the administrator's password is deleted even
    when the call that uses it fails; a token left by an earlier failure is deleted too."""

    def bootstrap(self, api: FakeApi) -> None:
        answers = iter(["ID Username\n1 dokopilot-admin\n", "jeton-d-administration\n"])
        original = provision.compose
        provision.compose = lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=next(answers), stderr="")
        self.addCleanup(setattr, provision, "compose", original)
        api.call = self.first_login_refused(api)  # type: ignore[method-assign]
        provision.ensure_admin(provision.complete(payload()), api, provision.Run())

    @staticmethod
    def first_login_refused(api: FakeApi):  # noqa: ANN205
        inner, seen = api.call, []

        def call(method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200,), **extra: Any) -> tuple[int, Any]:
            if (method, path) == ("GET", "/user") and not seen:
                seen.append(True)
                api.calls.append((method, path, body))
                return 401, None  # the password of the secrets is not the current one yet
            return inner(method, path, body, ok, **extra)

        return call

    def test_the_token_is_deleted_after_use(self) -> None:
        api = FakeApi({}, {"dokopilot-admin": [{"id": 3, "name": "dokopilot-bootstrap-ancien"}, {"id": 4, "name": "autre"}]})
        self.bootstrap(api)
        self.assertEqual([path for method, path, _ in api.calls if method == "DELETE"], ["/users/dokopilot-admin/tokens/3"])

    def test_the_token_is_deleted_when_the_update_fails(self) -> None:
        api = FakeApi({}, {"dokopilot-admin": [{"id": 5, "name": "dokopilot-bootstrap-0a1b2c3d"}]}, refuse=("PATCH",))
        with self.assertRaises(provision.Failure):
            self.bootstrap(api)
        self.assertEqual([path for method, path, _ in api.calls if method == "DELETE"], ["/users/dokopilot-admin/tokens/5"])


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
