# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of bin/dokopilot without a server: init, secrets, merge, inventory, refusals,
people (--person), server.local and sync (against local bare repositories).

Run with the Python of .venv (PyYAML): `.venv/bin/python -m unittest discover -s tests/cli`.
Needs `age`, `age-keygen` and `git` on the PATH.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "bin" / "dokopilot"
LUCAS = {"id": "lucas", "email": "lucas.bernard@atelier-demo.test", "first_name": "Lucas", "last_name": "Bernard", "role": "membre"}


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

    def cli(self, *args: str, check: bool = True, env: dict[str, str] | None = None, answers: str = "") -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(CLI), *args], env={**self.env, **(env or {})}, text=True, capture_output=True,
                                input=answers)
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

    # ----------------------------------------------------------------------- people

    def add_local(self, folder: Path, settings: dict) -> None:
        """Deep-merges settings into client.local.yml, as an integrator would edit it."""
        local = folder / "client.local.yml"
        current = yaml.safe_load(local.read_text(encoding="utf-8")) if local.exists() else {}
        cli = load_cli()
        local.write_text(yaml.safe_dump(cli.deep_merge(current or {}, settings), allow_unicode=True), encoding="utf-8")

    def in_process(self):
        """The CLI module, reading the clients of this test."""
        os.environ["DOKOPILOT_CLIENTS_DIR"] = self.env["DOKOPILOT_CLIENTS_DIR"]
        self.addCleanup(os.environ.pop, "DOKOPILOT_CLIENTS_DIR", None)
        return load_cli()

    def test_config_derives_the_keys_of_version_0_1_from_the_people(self) -> None:
        self.init_client()
        out = self.cli("config", "acme").stdout
        self.assertIn("assistant_email: assistant.camille@atelier-demo.test", out)
        self.assertRegex(out, r"(?m)^ +entrepreneur:$")
        self.assertRegex(out, r"(?m)^ +dirigeant:$")
        self.assertRegex(out, r"for: camille\.martin@atelier-demo\.test")

    def test_old_users_format_is_refused_with_the_new_form(self) -> None:
        folder = self.init_client()
        self.add_local(folder, {"users": {"entrepreneur": {"email": "alex@example.test", "first_name": "Alex", "last_name": "Test"}}})
        result = self.cli("config", "acme", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("users.entrepreneur et users.assistants ne sont plus acceptés", result.stderr)
        self.assertIn("  people:\n    - id: alex\n      email: alex@example.test\n", result.stderr)
        self.assertIn("      role: dirigeant\n", result.stderr)

    def test_config_role_refuses_the_old_users_format_too(self) -> None:
        """Playbooks run without the CLI (tests, automation) are protected by the role itself."""
        root = Path(self.tmp.name)
        (root / "inventory.yml").write_text("dokopilot:\n  hosts:\n    demo:\n      ansible_connection: local\n", encoding="utf-8")
        old = yaml.safe_load((REPO / "tests" / "config" / "base.yml").read_text(encoding="utf-8"))
        old["users"] = {"entrepreneur": {"email": "alex@example.test", "first_name": "Alex"}, "assistants": []}
        (root / "old.yml").write_text(yaml.safe_dump(old), encoding="utf-8")
        result = subprocess.run(
            [str(REPO / ".venv" / "bin" / "ansible-playbook"), "-i", str(root / "inventory.yml"), "-e", f"@{root / 'old.yml'}",
             "-e", f"@{REPO / 'tests' / 'config' / 'secrets.yml'}", "playbooks/config.yml"],
            cwd=REPO / "ansible", env={**self.env, "ANSIBLE_CONFIG": "ansible.cfg"}, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ne sont plus acceptés : déclarez chaque personne dans users.people", result.stdout + result.stderr)

    def run_harness(self, *args: str) -> list[str]:
        """Runs `harness` in process, without Ansible; returns the extra arguments passed to it."""
        cli = self.in_process()
        calls: list[list[str]] = []
        cli.ansible_playbook = lambda client, playbook, ns, bootstrap=False, extra=None: calls.append(extra or []) or 0
        code = cli.main(["harness", "acme", *args])
        self.assertEqual(code, 0)
        return calls[0]

    def test_harness_person_defaults_to_the_dirigeant(self) -> None:
        folder = self.init_client()
        self.add_local(folder, {"users": {"people": [LUCAS, *self.camille()]}})
        extra = self.run_harness()
        self.assertIn("dokopilot_person=camille", extra)
        self.assertEqual(extra[extra.index("dokopilot_person=camille") - 1], "-e")

    def test_harness_person_explicit_and_unknown(self) -> None:
        folder = self.init_client()
        self.add_local(folder, {"users": {"people": [*self.camille(), LUCAS]}})
        self.assertIn("dokopilot_person=lucas", self.run_harness("--person", "lucas", "--dest", str(Path(self.tmp.name) / "ws")))
        cli = self.in_process()
        cli.ansible_playbook = lambda *a, **k: self.fail("Ansible must not run for an unknown person")
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["harness", "acme", "--person", "inconnu foo=bar"]), 2)
        self.assertIn("Personne « inconnu foo=bar » inconnue", err.getvalue())
        self.assertIn("(camille, lucas)", err.getvalue())

    def camille(self) -> list[dict]:
        example = yaml.safe_load((REPO / "config" / "client.example.yml").read_text(encoding="utf-8"))
        return example["users"]["people"]

    # ----------------------------------------------------------------------- server.local

    def test_server_local_uses_a_local_connection_and_the_sudo_password_of_k(self) -> None:
        folder = self.init_client()
        self.add_local(folder, {"server": {"local": True}})
        cli = self.in_process()
        settings = cli.client_settings("acme")
        host = cli.inventory("acme", settings, user=None, local=False, bootstrap=False)["dokopilot"]["hosts"]["acme"]
        self.assertEqual(host, {"ansible_connection": "local"})
        result = self.cli("ssh", "acme", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("server.local: true", result.stderr)

    def test_ask_become_pass_is_not_overridden_by_the_secret(self) -> None:
        self.init_client()
        cli = self.in_process()
        settings = cli.client_settings("acme")
        host = cli.inventory("acme", settings, user=None, local=False, bootstrap=False, ask_become_pass=True)["dokopilot"]["hosts"]["acme"]
        self.assertEqual(host["ansible_host"], "192.0.2.5")
        self.assertNotIn("ansible_become_password", host)

    # ----------------------------------------------------------------------- sync

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, text=True, capture_output=True, check=True).stdout

    def commit(self, clone: Path, name: str, text: str, message: str) -> None:
        (clone / name).write_text(text, encoding="utf-8")
        self.git(clone, "add", name)
        self.git(clone, "commit", "-q", "-m", message)

    def credentials_file(self) -> Path:
        return Path(self.tmp.name) / "home" / ".config" / "dokopilot" / "acme" / "git-credentials"

    def make_workspace(self) -> tuple[Path, Path, Path]:
        """Bare repositories entreprise and perso-camille, the workspace (its clones) and
        another computer's clone of entreprise. Returns (workspace, bare repositories, other)."""
        root = Path(self.tmp.name)
        self.env.update(HOME=str(root / "home"), GIT_CONFIG_GLOBAL=str(root / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        (root / "home").mkdir()
        for key, value in (("user.name", "Test"), ("user.email", "test@example.test"), ("init.defaultBranch", "main")):
            self.git(root, "config", "--global", key, value)
        bare = root / "forgejo"
        workspace = root / "home" / "DokoPilot" / "acme"
        for repo, clone, content in (("entreprise", workspace, "direction/\nperso/\n"), ("perso-camille", workspace / "perso", "")):
            self.git(root, "init", "-q", "--bare", str(bare / f"{repo}.git"))
            seed = root / "seed" / repo
            self.git(root, "clone", "-q", str(bare / f"{repo}.git"), str(seed))
            self.commit(seed, ".gitignore" if content else "memoire-vive.md", content or "# Mémoire vive\n", "Contexte initial")
            self.git(seed, "push", "-q", "origin", "HEAD")
            self.git(root, "clone", "-q", str(bare / f"{repo}.git"), str(clone))
            # As the harness role configures them: the store helper on the private file only.
            self.git(clone, "config", "--local", "--add", "credential.helper", "")
            self.git(clone, "config", "--local", "--add", "credential.helper", f"store --file {self.credentials_file()}")
        other = root / "other"
        self.git(root, "clone", "-q", str(bare / "entreprise.git"), str(other))
        return workspace, bare, other

    def test_sync_pulls_then_pushes_each_clone_of_the_default_workspace(self) -> None:
        self.init_client()
        workspace, bare, other = self.make_workspace()
        self.commit(other, "note.md", "Note d'un collègue\n", "Note reçue")
        self.git(other, "push", "-q")
        self.commit(workspace, "00-Boite-de-reception.md", "Note locale\n", "Note de l'assistant")
        self.commit(workspace / "perso", "memoire-vive.md", "Préférences\n", "Mémoire")
        (workspace / "perso" / "brouillon.md").write_text("pas encore enregistré\n", encoding="utf-8")
        result = self.cli("sync", "acme", answers="o\noui\n")
        self.assertIn(f"Synchronisation de {workspace} pour Camille Martin (camille)", result.stdout)
        # What is sent is shown before the person accepts it.
        self.assertIn("« entreprise » : 1 enregistrement(s) à envoyer vers Forgejo", result.stdout)
        self.assertIn("00-Boite-de-reception.md | 1 +", result.stdout)
        self.assertIn("✓ entreprise : 1 changement(s) reçu(s), 1 envoyé(s)", result.stdout)
        self.assertIn("· direction : aucune copie sur ce poste", result.stdout)
        self.assertIn("✓ perso : 1 envoyé(s) ; 1 nouveau(x) fichier(s) non enregistré(s)", result.stdout)
        self.assertTrue((workspace / "note.md").exists())
        self.assertIn("Note de l'assistant", self.git(bare / "entreprise.git", "log", "--format=%s", "main"))
        self.assertIn("Mémoire", self.git(bare / "perso-camille.git", "log", "--format=%s", "main"))
        # The rebased commit keeps its author and is committed by the person who synchronises.
        self.assertEqual(self.git(workspace, "log", "-1", "--format=%an|%cn|%ce").strip(), "Test|Camille Martin|camille.martin@atelier-demo.test")
        again = self.cli("sync", "acme", "--person", "camille")
        self.assertIn("✓ entreprise : déjà à jour", again.stdout)

    def test_sync_stops_at_the_first_conflict(self) -> None:
        self.init_client()
        workspace, bare, other = self.make_workspace()
        dest = Path(self.tmp.name) / "ailleurs"
        workspace.rename(dest)
        self.commit(other, "fiche.md", "Version du collègue\n", "Fiche du collègue")
        self.git(other, "push", "-q")
        self.commit(dest, "fiche.md", "Version locale\n", "Fiche locale")
        self.commit(dest / "perso", "memoire-vive.md", "Préférences\n", "Mémoire")
        result = self.cli("sync", "acme", "--dest", str(dest), check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"Conflit dans « entreprise » ({dest})", result.stderr)
        self.assertIn("fiche.md", result.stderr)
        self.assertIn("rebase --abort", result.stderr)
        self.assertNotIn("Fiche locale", self.git(bare / "entreprise.git", "log", "--format=%s", "main"))
        self.assertNotIn("Mémoire", self.git(bare / "perso-camille.git", "log", "--format=%s", "main"))
        again = self.cli("sync", "acme", "--dest", str(dest), check=False)
        self.assertEqual(again.returncode, 2)
        self.assertIn("Conflit dans « entreprise »", again.stderr)

    def test_sync_refuses_uncommitted_changes_and_a_missing_workspace(self) -> None:
        self.init_client()
        workspace, bare, _ = self.make_workspace()
        (workspace / ".gitignore").write_text("modifié\n", encoding="utf-8")
        result = self.cli("sync", "acme", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("des fichiers modifiés ne sont pas encore enregistrés dans Git : .gitignore", result.stderr)
        missing = self.cli("sync", "acme", "--dest", str(Path(self.tmp.name) / "vide"), check=False)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("Aucune copie Git", missing.stderr)

    def test_sync_runs_no_planted_program_and_refuses_changed_git_settings(self) -> None:
        self.init_client()
        workspace, bare, _ = self.make_workspace()
        marker = Path(self.tmp.name) / "hook-ran"
        hook = workspace / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
        hook.chmod(0o755)
        self.commit(workspace, "note.md", "Note\n", "Note de l'assistant")
        result = self.cli("sync", "acme", "--yes", env={"GIT_AUTHOR_NAME": "Assistant", "GIT_COMMITTER_NAME": "Assistant"})
        self.assertIn("✓ entreprise : 1 envoyé(s)", result.stdout)
        self.assertFalse(marker.exists(), "a hook planted in the clone ran during sync")
        # Settings an agent could write: each one stops sync before anything is sent.
        for key, value, expected in (
            ("core.sshCommand", "touch /tmp/x", "réglages Git inattendus : core.sshcommand"),
            ("remote.origin.pushurl", "/tmp/elsewhere.git", "réglages Git inattendus : remote.origin.pushurl"),
            ("credential.helper", "!touch /tmp/x", "credential.helper n'est pas celui de DokoPilot"),
        ):
            with self.subTest(key=key):
                self.git(workspace, "config", "--local", "--add", key, value)
                self.commit(workspace, f"{key}.md", "Note\n", f"Note {key}")
                refused = self.cli("sync", "acme", check=False)
                self.assertEqual(refused.returncode, 2)
                self.assertIn(expected, refused.stderr)
                self.assertRegex(refused.stderr, "[Rr]ien n'a été envoyé")
                self.assertNotIn(f"Note {key}", self.git(bare / "entreprise.git", "log", "--format=%s", "main"))
                self.git(workspace, "config", "--local", "--unset-all", key)
                if key == "credential.helper":
                    self.git(workspace, "config", "--local", "--add", "credential.helper", "")
                    self.git(workspace, "config", "--local", "--add", "credential.helper", f"store --file {self.credentials_file()}")
        # A branch that follows another remote is refused too.
        self.git(workspace, "config", "--local", "branch.main.remote", "elsewhere")
        other_remote = self.cli("sync", "acme", "--yes", check=False)
        self.assertEqual(other_remote.returncode, 2)
        self.assertIn("branch.main.remote=elsewhere", other_remote.stderr)
        self.git(workspace, "config", "--local", "branch.main.remote", "origin")
        # An HTTP remote must be the Forgejo of the credential file.
        self.credentials_file().parent.mkdir(parents=True, exist_ok=True)
        self.credentials_file().write_text("http://camille:jeton@forgejo.example.test%3a3000\n", encoding="utf-8")
        self.git(workspace, "remote", "set-url", "origin", "http://ailleurs.example.test/acme/entreprise.git")
        elsewhere = self.cli("sync", "acme", check=False)
        self.assertEqual(elsewhere.returncode, 2)
        self.assertIn("n'est pas celle de Forgejo", elsewhere.stderr)
        self.assertNotIn("jeton", elsewhere.stderr + elsewhere.stdout)

    def test_sync_sends_nothing_the_person_refuses_and_asks_before_taking_new_rules(self) -> None:
        self.init_client()
        workspace, bare, other = self.make_workspace()
        self.commit(workspace, "brouillon.md", "Note de l'agent\n", "Copie d'une note de direction")
        refused = self.cli("sync", "acme")  # no answer: nothing is sent
        self.assertIn("envoi refusé, 1 enregistrement(s) restent sur ce poste", refused.stdout)
        self.assertNotIn("Copie d'une note", self.git(bare / "entreprise.git", "log", "--format=%s", "main"))
        # A colleague changes the rules of the agents: shown, and asked even with --yes.
        self.commit(other, "AGENTS.md", "Nouvelle règle\n", "Règle changée")
        self.git(other, "pull", "-q", "--rebase")
        self.git(other, "push", "-q")
        head = self.git(workspace, "rev-parse", "HEAD")
        kept = self.cli("sync", "acme", "--yes", check=False)
        self.assertEqual(kept.returncode, 2)
        self.assertIn("les règles des agents ont changé sur le serveur (AGENTS.md)", kept.stdout)
        self.assertIn("Règle changée", kept.stdout)
        self.assertIn("règles refusées ; rien n'a été reçu ni envoyé", kept.stderr)
        self.assertEqual(self.git(workspace, "rev-parse", "HEAD"), head)
        accepted = self.cli("sync", "acme", "--yes", answers="o\n")
        self.assertIn("✓ entreprise : 1 changement(s) reçu(s), 1 envoyé(s)", accepted.stdout)
        self.assertTrue((workspace / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
