# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fast tests of installer/wizard.py, without containers or network.

Run: .venv/bin/python -m unittest discover -s tests/installer
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("wizard", REPO / "installer" / "wizard.py")
assert spec and spec.loader
wizard = importlib.util.module_from_spec(spec)
sys.modules["wizard"] = wizard
spec.loader.exec_module(wizard)

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl camille@portable"


class Validators(unittest.TestCase):
    def test_siren_check_digit(self) -> None:
        self.assertEqual(wizard.check_siren("732 829 320"), "732829320")
        with self.assertRaises(wizard.Invalid):
            wizard.check_siren("732829321")
        with self.assertRaises(wizard.Invalid):
            wizard.check_siren("12345")

    def test_client_id_refuses_the_versioned_example(self) -> None:
        self.assertEqual(wizard.check_client_id("atelier-martin"), "atelier-martin")
        for bad in ("demo", "Atelier", "-x", "a"):
            with self.assertRaises(wizard.Invalid):
                wizard.check_client_id(bad)

    def test_models_get_the_provider_prefix(self) -> None:
        parse = wizard.model_parser("openrouter")
        self.assertEqual(parse("anthropic/claude-x"), "openrouter/anthropic/claude-x")
        self.assertEqual(parse("openrouter/a/b"), "openrouter/a/b")
        self.assertEqual(parse(""), "")
        with self.assertRaises(wizard.Invalid):
            parse("deux mots")

    def test_maintenance_account_must_be_nominative(self) -> None:
        self.assertEqual(wizard.check_maintenance_user("camille"), "camille")
        for bad in ("root", "debian", "ubuntu", "Camille"):
            with self.assertRaises(wizard.Invalid):
                wizard.check_maintenance_user(bad)

    def test_names_ids_and_abbreviations(self) -> None:
        self.assertEqual(wizard.slug("Élodie"), "elodie")
        self.assertEqual(wizard.abbreviation("Atelier Démo"), "AD")
        self.assertEqual(wizard.abbreviation("Menuiserie de la Gare"), "MG")
        self.assertEqual(wizard.check_email("Camille@Exemple.fr"), "camille@exemple.fr")
        with self.assertRaises(wizard.Invalid):
            wizard.check_email("camille@exemple")

    def test_ssh_keys_lose_their_options(self) -> None:
        self.assertEqual(wizard.ssh_key_from_text(f'from="10.0.0.1",no-pty {KEY}'), KEY)
        self.assertEqual(wizard.check_key_source(KEY), KEY)
        self.assertEqual(wizard.check_key_source(""), "")
        with self.assertRaises(wizard.Invalid):
            wizard.check_key_source("ssh-ed25519 trop-court")

    def test_authorized_keys_are_read_and_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorized_keys"
            path.write_text(f"# comment\n{KEY}\nno-pty {KEY}\n\n", encoding="utf-8")
            self.assertEqual(wizard.authorized_keys(path), ([KEY], 1))

    def test_secret_checks(self) -> None:
        wizard.check_tailscale_key("tskey-auth-abc")
        with self.assertRaises(wizard.Invalid):
            wizard.check_tailscale_key("abc")
        with self.assertRaises(wizard.Invalid):
            wizard.check_api_key("court")


class ScriptedAnswers(unittest.TestCase):
    def ui(self, answers: dict) -> wizard.Ui:
        return wizard.Ui(answers, "reponses.yml", False)

    def quiet(self) -> contextlib.AbstractContextManager:
        return contextlib.redirect_stdout(io.StringIO())

    def test_choices_take_numbers_words_and_aliases(self) -> None:
        options = [wizard.Option("local", "ici"), wizard.Option("distant", "ailleurs", ("remote",))]
        with self.quiet():
            self.assertEqual(self.ui({"where": 2}).choice("where", "Où ?", options, "local"), "distant")
            self.assertEqual(self.ui({"where": "Remote"}).choice("where", "Où ?", options, "local"), "distant")
            self.assertEqual(self.ui({}).choice("where", "Où ?", options, "local"), "local")

    def test_yes_no_accepts_french_and_yaml_booleans(self) -> None:
        with self.quiet():
            self.assertTrue(self.ui({"a": "oui"}).yes_no("a", "?", False))
            self.assertFalse(self.ui({"a": False}).yes_no("a", "?", True))

    def test_missing_answer_without_default_stops(self) -> None:
        with self.quiet(), self.assertRaises(wizard.Stop) as stop:
            self.ui({}).ask("server.address", "Adresse")
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("server.address", str(stop.exception))

    def test_invalid_answer_stops_with_the_reason(self) -> None:
        with self.quiet(), self.assertRaises(wizard.Stop) as stop:
            self.ui({"company": {"siren": "123"}}).ask("company.siren", "SIREN", parse=wizard.check_siren)
        self.assertIn("9 chiffres", str(stop.exception))

    def test_secrets_come_from_the_environment_or_a_file_never_the_answers(self) -> None:
        with self.quiet():
            with mock.patch.dict(os.environ, {"CLE": "sk-0123456789"}):
                self.assertEqual(self.ui({"models": {"api_key_env": "CLE"}}).secret("models.api_key", "Clé", required=True), "sk-0123456789")
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
                handle.write("tskey-auth-xyz\n")
            try:
                value = self.ui({"tailscale_auth_key_file": handle.name}).secret("tailscale_auth_key", "Clé", required=True)
                self.assertEqual(value, "tskey-auth-xyz")
            finally:
                os.unlink(handle.name)
            with self.assertRaises(wizard.Stop) as stop:
                self.ui({"models": {"api_key": "sk-in-clear-0123"}}).secret("models.api_key", "Clé", required=True)
            self.assertNotIn("sk-in-clear-0123", str(stop.exception))
            with self.assertRaises(wizard.Stop):
                self.ui({}).secret("models.api_key", "Clé", required=True)
            self.assertEqual(self.ui({}).secret("x", "Clé", required=False), "")


class FilesAndState(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patches = [
            mock.patch.dict(os.environ, {"DOKOPILOT_CLIENTS_DIR": str(root / "clients")}),
            mock.patch.object(wizard, "CONFIG_ROOT", root / "config" / "dokopilot"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.root = root

    def test_private_files_are_0600_in_a_0700_folder(self) -> None:
        path = wizard.CONFIG_ROOT / "atelier" / "model-api-key"
        wizard.write_private(path, "sk-0123456789")
        self.assertEqual(path.read_text(encoding="utf-8"), "sk-0123456789")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_state_is_saved_once_the_client_is_known_and_resumed(self) -> None:
        state = wizard.State()
        state.mark("lieu")
        self.assertFalse((self.root / "clients").exists())
        state.answers["client_id"] = "atelier"
        state.pending["tailscale_auth_key"] = "tskey-auth-secret"
        state.mark("profil")
        path = self.root / "clients" / "atelier" / wizard.STATE_NAME
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn("tskey-auth-secret", path.read_text(encoding="utf-8"))
        ui = wizard.Ui({"client_id": "atelier"}, "reponses.yml", False)
        with contextlib.redirect_stdout(io.StringIO()):
            resumed = wizard.choose_state(ui)
        self.assertEqual(resumed.completed, ["lieu", "profil"])
        ui = wizard.Ui({"client_id": "atelier", "resume": "non"}, "reponses.yml", False)
        with contextlib.redirect_stdout(io.StringIO()):
            fresh = wizard.choose_state(ui)
        self.assertEqual(fresh.completed, [])
        self.assertFalse(path.exists())

    def test_settings_written_for_a_local_server(self) -> None:
        answers = {
            "where": "local",
            "maintenance_user": "camille",
            "full_name": "Camille Martin",
            "ssh_public_keys": [KEY],
            "hostname": "serveur",
        }
        local = wizard.local_settings(answers)
        self.assertEqual(local["server"], {"local": True, "address": "localhost", "hostname": "serveur"})
        self.assertEqual(local["maintenance"]["users"], [{"name": "camille", "full_name": "Camille Martin", "ssh_public_keys": [KEY]}])

    def test_secret_editor_merges_values_read_on_stdin(self) -> None:
        clear = self.root / "secrets.yml"
        clear.write_text("secrets:\n  tailscale_auth_key: ''\n  restic_env: {}\n  db_root_password: x\n", encoding="utf-8")
        values = {"tailscale_auth_key": "tskey-auth-1", "restic_env": {"AWS_ACCESS_KEY_ID": "id"}}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(values))):
            self.assertEqual(wizard.edit_secrets_file(clear), 0)
        data = wizard.yaml.safe_load(clear.read_text(encoding="utf-8"))["secrets"]
        self.assertEqual(data, {"tailscale_auth_key": "tskey-auth-1", "restic_env": {"AWS_ACCESS_KEY_ID": "id"}, "db_root_password": "x"})
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"db_root_password": "y"}))), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(wizard.edit_secrets_file(clear), 3)


if __name__ == "__main__":
    unittest.main()
