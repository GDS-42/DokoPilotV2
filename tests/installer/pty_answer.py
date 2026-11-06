#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run a command in a pseudo-terminal and answer Ansible's sudo prompts (tests only).

    pty_answer.py PASSWORD_FILE LOG_FILE -- COMMAND [ARGS...]

Every time the output shows « BECOME password: », the first line of PASSWORD_FILE is typed,
as the person would type it at the terminal. The output goes to LOG_FILE, never the password.
Exit status: the command's.
"""

from __future__ import annotations

import os
import pty
import select
import sys

PROMPT = b"BECOME password:"


def main() -> int:
    password_file, log_file = sys.argv[1], sys.argv[2]
    if sys.argv[3] != "--":
        sys.exit("usage: pty_answer.py PASSWORD_FILE LOG_FILE -- COMMAND [ARGS...]")
    command = sys.argv[4:]
    with open(password_file, "rb") as handle:
        password = handle.readline().rstrip(b"\n")
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(command[0], command)
    window = b""
    with open(log_file, "ab") as log:
        while True:
            try:
                ready, _, _ = select.select([fd], [], [], 600)
            except InterruptedError:
                continue
            if not ready:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            log.write(chunk)
            log.flush()
            window = (window + chunk)[-256:]
            if PROMPT in window:
                os.write(fd, password + b"\n")
                window = b""
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
