# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers shared by the provisioning steps (run inside the Dokos backend container)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

BENCH_PATH = "/home/frappe/frappe-bench"
SITES_PATH = f"{BENCH_PATH}/sites"


class StepError(Exception):
    """A failure explained to the integrator (message in French)."""


@contextmanager
def site_connection(site: str, user: str = "Administrator") -> Iterator[Any]:
    """Connect to `site` as `user`, commit on success, roll back on error."""
    import frappe

    os.chdir(SITES_PATH)
    frappe.init(site=site, sites_path=SITES_PATH)
    frappe.connect()
    frappe.set_user(user)
    frappe.local.lang = frappe.db.get_default("lang") or "en"
    try:
        yield frappe
        frappe.db.commit()
    except BaseException:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


def site_exists(site: str) -> bool:
    return os.path.exists(os.path.join(SITES_PATH, site, "site_config.json"))


def require_site(site: str) -> None:
    if not site_exists(site):
        raise StepError(f"Le site Dokos « {site} » n'existe pas : lancez d'abord l'installation (dokos.yml).")


def wait_until(check: Callable[[], bool], timeout: int, interval: float = 3.0) -> bool:
    """Poll `check` until it is true or `timeout` seconds have passed.

    Each poll starts a new transaction, so that rows committed by the background
    workers become visible (MariaDB runs in REPEATABLE READ).
    """
    import frappe

    deadline = time.monotonic() + timeout
    while True:
        frappe.db.rollback()
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def recent_errors(limit: int = 3) -> list[str]:
    """Titles of the latest Error Log entries, to explain a silent failure."""
    import frappe

    try:
        rows = frappe.get_all("Error Log", fields=["method", "creation"], order_by="creation desc", limit=limit)
    except Exception:
        return []
    return [str(row.method) for row in rows]


def existing_doctypes(names: list[str]) -> tuple[list[str], list[str]]:
    import frappe

    present = [name for name in names if frappe.db.exists("DocType", name)]
    return present, [name for name in names if name not in present]
