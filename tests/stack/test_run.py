# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit checks of stack/provisioning/run.py that need no Dokos: what is masked in the
output of a provisioning step.

Run: .venv/bin/python -m unittest discover -s tests/stack -p 'test_*.py'
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

PROVISIONING = Path(__file__).resolve().parents[2] / "stack" / "provisioning"
sys.path.insert(0, str(PROVISIONING))
SPEC = importlib.util.spec_from_file_location("provisioning_run", PROVISIONING / "run.py")
assert SPEC and SPEC.loader
run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run)


class MaskingTest(unittest.TestCase):
    def test_secrets_are_found_at_any_depth(self) -> None:
        payload = {
            "site": "demo.dokopilot.internal",
            "password": "mot-de-passe-admin",
            "existing_credentials": [{"user": "assistant@exemple.test", "api_key": "0123456789abcdef", "api_secret": "fedcba9876543210"}],
            # The documented form of the Dokos AI keys: the secret is under the provider's name.
            "provider_keys": {"mistral": "cle-mistral-0123456789"},
            "providers": [{"provider": "mistral", "default_model": "mistral-large-4"}],
            "language": "fr",
        }
        secrets = run.secret_values(payload)
        for secret in ("mot-de-passe-admin", "0123456789abcdef", "fedcba9876543210", "cle-mistral-0123456789"):
            self.assertIn(secret, secrets)
        for ordinary in ("demo.dokopilot.internal", "mistral", "mistral-large-4", "fr", "assistant@exemple.test"):
            self.assertNotIn(ordinary, secrets)
        message = run.mask("Erreur : la clé cle-mistral-0123456789 est refusée pour mistral", secrets)
        self.assertEqual(message, "Erreur : la clé *** est refusée pour mistral")

    def test_a_short_word_under_a_secret_section_is_not_masked_everywhere(self) -> None:
        self.assertEqual(run.secret_values({"provider_keys": {"ollama": "none"}}), [])


if __name__ == "__main__":
    unittest.main()
