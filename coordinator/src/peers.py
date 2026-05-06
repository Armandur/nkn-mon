"""Peer-tilldelning för Iteration 3.

För varje probe: plocka N andra probes på olika /24-subnet att mäta mot.
Rotation per dygn baserad på hash av (probe_id + datum) så peers byts ut
periodvis utan att alla probes byter samtidigt.
"""
from __future__ import annotations

import ipaddress
import random
from datetime import datetime, timezone
from typing import Iterable


def _subnets_of(ips: Iterable[str], prefix: int = 24) -> set[str]:
    """Returnera unika /24-prefix för en lista IP:n."""
    out: set[str] = set()
    for ip in ips or []:
        try:
            net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
            out.add(str(net))
        except ValueError:
            continue
    return out


def assign_peers(
    probe: dict,
    all_probes: list[dict],
    count: int = 3,
    today: str | None = None,
) -> list[dict]:
    """Plocka 'count' peers för en probe.

    - Filtrerar bort probes på samma /24 som probe (samma site)
    - Filtrerar bort probes utan local_ipv4 eller som inte klassats som NKN
    - Filtrerar bort sig själv
    - Roterar genom deterministisk hash av (probe_id + dagens datum)
    """
    if today is None:
        today = datetime.now(timezone.utc).date().isoformat()

    my_subnets = _subnets_of(probe.get("last_local_ipv4") or [])

    candidates = []
    for p in all_probes:
        if p["id"] == probe["id"]:
            continue
        if not p.get("last_local_ipv4"):
            continue
        if p.get("last_classification") != "nkn":
            continue
        if not p.get("enabled", True):
            continue
        their_subnets = _subnets_of(p["last_local_ipv4"])
        if my_subnets & their_subnets:
            continue
        candidates.append(p)

    if not candidates:
        return []

    # Deterministisk shuffle baserad på probe + datum så urvalet roterar
    seed_str = f"{probe['id']}-{today}"
    rng = random.Random(seed_str)
    rng.shuffle(candidates)
    return candidates[:count]
