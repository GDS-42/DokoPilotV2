#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Editor that installer/wizard.py gives to « bin/dokopilot secrets <client> edit » (EDITOR):
# it merges into the decrypted secrets file the values the assistant sends on standard input
# (JSON), so that no secret goes through a command line or an environment variable. The CLI
# then encrypts the file again. Not meant to be run by hand.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
exec "$here/../.venv/bin/python" "$here/wizard.py" --secret-editor "$1"
