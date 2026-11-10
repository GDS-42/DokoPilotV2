# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of bin/dokopilot without a server: init, secrets, merge, inventory, refusals.

Run with the Python of .venv (PyYAML): `.venv/bin/python -m unittest discover -s tests/cli`.
Needs `age` and `age-keygen` on the PATH.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "bin" / "dokopilot"


def load_cli():
    loader = importlib.machinery.SourceFileLoader("dokopilot_cli", str(CLI))
    spec = importlib.util.spec_from_loader("dokopilot_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = dict(os.environ)
        self.env.update(
            DOKOPILOT_CLIENTS_DIR=str(root / "clients"),
            DOKOPILOT_AGE_IDENTITY=str(root / "age-identity.txt"),
            ANSIBLE_NOCOLOR="1",
            ANSIBLE_FORCE_COLOR="0",
        )
        self.env.pop("DOKOPILOT_SSH_KEY", None)
        self.env.pop("DOKOPILOT_SSH_USER", None)
        self.key = root / "id_ed25519"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        self.clients = root / "clients"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(CLI), *args], env=self.env, text=True, capture_output=True)
        if check and result.returncode != 0:
            self.fail(f"bin/dokopilot {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
        return result

    def init_client(self) -> Path:
        self.cli("init", "acme", "--address", "192.0.2.5", "--maintenance-user", "bob", "--ssh-key", f"{self.key}.pub")
        return self.clients / "acme"

    def test_init_creates_settings_recipients_and_encrypted_secrets(self) -> None:
        folder = self.init_client()
        self.assertTrue((folder / "client.yml").exists())
        self.assertTrue((folder / "recipients.txt").read_text().startswith("age1"))
        armored = (folder / "secrets.yml.age").read_text()
        self.assertIn("BEGIN AGE ENCRYPTED FILE", armored)
        self.assertNotIn("generate", armored)
        local = yaml.safe_load((folder / "client.local.yml").read_text())
        self.assertEqual(local["server"]["address"], "192.0.2.5")
        self.assertEqual(local["maintenance"]["users"][0]["name"], "bob")
        keys = self.cli("secrets", "acme", "keys").stdout.split()
        self.assertIn("restic_password", keys)
        password = self.cli("secrets", "acme", "show", "restic_password").stdout.strip()
        self.assertGreaterEqual(len(password), 24)
        self.assertNotEqual(password, "generate")

    def test_init_twice_keeps_the_secrets(self) -> None:
        folder = self.init_client()
        before = (folder / "secrets.yml.age").read_bytes()
        self.init_client()
        self.assertEqual(before, (folder / "secrets.yml.age").read_bytes())

    def test_config_accepts_the_initialised_demo_client(self) -> None:
        self.init_client()
        result = self.cli("config", "acme")
        self.assertIn("address: 192.0.2.5", result.stdout)
        self.assertIn("name: bob", result.stdout)

    def test_unknown_top_level_setting_is_refused(self) -> None:
        folder = self.init_client()
        with (folder / "client.local.yml").open("a", encoding="utf-8") as handle:
            handle.write("maintenance_extra_groups: [docker]\n")
        result = self.cli("config", "acme", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Réglages inconnus", result.stderr)

    def test_inventory_uses_the_account_key_and_trusts_new_hosts_only(self) -> None:
        self.init_client()
        os.environ["DOKOPILOT_CLIENTS_DIR"] = self.env["DOKOPILOT_CLIENTS_DIR"]
        try:
            cli = load_cli()
            settings = cli.client_settings("acme")
            host = cli.inventory("acme", settings, user=None, local=False, bootstrap=False)["dokopilot"]["hosts"]["acme"]
        finally:
            os.environ.pop("DOKOPILOT_CLIENTS_DIR", None)
        self.assertEqual(host["ansible_user"], "bob")
        self.assertEqual(host["ansible_host"], "192.0.2.5")
        self.assertEqual(host["ansible_ssh_private_key_file"], str(self.key))
        self.assertIn("StrictHostKeyChecking=accept-new", host["ansible_ssh_common_args"])
        self.assertIn("IdentitiesOnly=yes", host["ansible_ssh_common_args"])
        self.assertEqual(host["ansible_become_password"], "{{ secrets.maintenance_sudo_password }}")

    def test_deep_merge_keeps_sibling_settings(self) -> None:
        cli = load_cli()
        merged = cli.deep_merge({"server": {"address": "a", "ssh_port": 2222}}, {"server": {"address": "b"}})
        self.assertEqual(merged, {"server": {"address": "b", "ssh_port": 2222}})


if __name__ == "__main__":
    unittest.main()
