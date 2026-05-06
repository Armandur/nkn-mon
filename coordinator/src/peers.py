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

    - Anchors (role=anchor) inkluderas alltid, oberoende av shuffle
    - Övriga probes filtreras på subnet/classification/enabled
    - Roterar bland icke-anchor-kandidater via (probe_id + dagens datum)
    """
    if today is None:
        today = datetime.now(timezone.utc).date().isoformat()

    my_subnets = _subnets_of(probe.get("last_local_ipv4") or [])

    anchors: list[dict] = []
    candidates: list[dict] = []
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
        if p.get("role") == "anchor":
            anchors.append(p)
        else:
            candidates.append(p)

    seed_str = f"{probe['id']}-{today}"
    rng = random.Random(seed_str)
    rng.shuffle(candidates)

    # Anchors först (alltid med), sen fyll på med roterade probes upp till count
    selected = anchors[:]
    remaining = max(0, count - len(selected))
    selected.extend(candidates[:remaining])
    return selected
