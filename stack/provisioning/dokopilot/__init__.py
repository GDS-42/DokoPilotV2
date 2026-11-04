# SPDX-License-Identifier: AGPL-3.0-or-later
"""DokoPilot provisioning steps for an unmodified Dokos v5 site.

Each step only uses documented Frappe/Dokos APIs (installer, permission manager,
setup wizard, whitelisted app methods) and changes nothing that already has the
wanted value, so that a second run reports no change.
"""
