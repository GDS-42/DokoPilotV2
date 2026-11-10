# SPDX-License-Identifier: AGPL-3.0-or-later
"""Network ranges of a client configuration, checked with the standard library.

A pattern on the first numbers of an address is not enough: « 10.0.0.0/1 » starts like a
private range and covers half of the Internet, and nftables accepts it.
"""

from __future__ import annotations

import ipaddress
from typing import Any

# RFC 1918, CGNAT (the addresses of Tailscale), IPv6 unique local addresses.
PRIVATE = tuple(ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))


def _network(value: Any) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """The network written `value` (an address or address/prefix, no host bit set), or None."""
    if not isinstance(value, str):
        return None
    try:
        return ipaddress.ip_network(value.strip(), strict=True) if value == value.strip() else None
    except ValueError:
        return None


def dokopilot_not_private_networks(values: Any) -> list[str]:
    """The entries that are not one network inside a private range."""
    if not isinstance(values, list):
        return [str(values)]
    bad = []
    for value in values:
        network = _network(value)
        inside = network is not None and any(network.version == private.version and network.subnet_of(private) for private in PRIVATE)
        if not inside:
            bad.append(str(value))
    return bad


def dokopilot_not_networks(values: Any) -> list[str]:
    """The entries that are not one network, or that cover every address (/0)."""
    if not isinstance(values, list):
        return [str(values)]
    bad = []
    for value in values:
        network = _network(value)
        if network is None or network.prefixlen == 0:
            bad.append(str(value))
    return bad


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "dokopilot_not_private_networks": dokopilot_not_private_networks,
            "dokopilot_not_networks": dokopilot_not_networks,
        }
