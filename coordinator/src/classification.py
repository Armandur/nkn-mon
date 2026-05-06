"""NKN-klassificering baserat på publik IP mot kända ranges från config."""
from __future__ import annotations

import ipaddress
from typing import Iterable


def classify_public_ip(public_ip: str | None, ranges: Iterable[str]) -> str:
    """Returnera 'nkn' / 'external' / 'unknown' baserat på publik IP."""
    if not public_ip:
        return "unknown"
    try:
        addr = ipaddress.ip_address(public_ip)
    except ValueError:
        return "unknown"
    for cidr in ranges:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if addr in net:
            return "nkn"
    return "external"
