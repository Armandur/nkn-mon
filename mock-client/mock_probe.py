"""Mock-klient för Iteration 1.

Simulerar N stycken probes som registrerar sig hos koordinatorn och var
MOCK_INTERVAL_SECONDS skickar in 1 ping-resultat med slumpat RTT 5-50 ms.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
from datetime import datetime, timezone

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nkn.mock")

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:8200").rstrip("/")
MOCK_PROBES = int(os.getenv("MOCK_PROBES", "5"))
INTERVAL = float(os.getenv("MOCK_INTERVAL_SECONDS", "30"))
SCENARIO = os.getenv("MOCK_SCENARIO", "normal")
REG_KEY = os.getenv("REGISTRATION_KEY", "dev-registration-key")

SITES = [
    ("EXP-FALUN-01", "Falun församlingsexpedition", "Falu pastorat"),
    ("KYRKA-LULEA-01", "Luleå domkyrka", "Luleå pastorat"),
    ("EXP-VAXJO-01", "Växjö stiftskansli", "Växjö stift"),
    ("KYRKA-VISBY-01", "Visby domkyrka", "Visby pastorat"),
    ("EXP-UPPSALA-01", "Uppsala stiftskansli", "Uppsala stift"),
    ("EXP-OSTERSUND-01", "Östersunds expedition", "Härnösands stift"),
    ("KYRKA-MALMO-01", "S:t Petri Malmö", "Lunds stift"),
    ("EXP-GBG-01", "Göteborgs stiftskansli", "Göteborgs stift"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sample_rtt() -> tuple[float, float, float, float, bool]:
    """Returnera (min, avg, max, loss_pct, success) enligt scenariot."""
    if SCENARIO == "degraded":
        avg = random.uniform(40, 200)
        loss = random.choice([0, 0, 5, 10, 25])
    elif SCENARIO == "offline-bursts":
        if random.random() < 0.15:
            return (0.0, 0.0, 0.0, 100.0, False)
        avg = random.uniform(5, 50)
        loss = 0
    else:
        avg = random.uniform(5, 50)
        loss = 0 if random.random() > 0.05 else random.choice([5, 25])

    spread = random.uniform(0.5, 3.0)
    rtt_min = max(0.1, avg - spread)
    rtt_max = avg + spread
    success = loss < 100
    return (rtt_min, avg, rtt_max, loss, success)


async def register(client: httpx.AsyncClient, idx: int) -> tuple[str, str, dict]:
    hostname, site_name, eccl = SITES[idx % len(SITES)]
    hostname = f"{hostname}-{idx}"
    payload = {
        "registration_key": REG_KEY,
        "client_metadata": {
            "hostname": hostname,
            "site_name": site_name,
            "ecclesiastical_unit": eccl,
            "site_type": "expedition" if hostname.startswith("EXP") else "kyrka",
            "notes": f"mock probe #{idx}",
        },
    }
    resp = await client.post(f"{COORDINATOR_URL}/probe/register", json=payload)
    resp.raise_for_status()
    data = resp.json()
    log.info("Probe %d registrerad client_id=%s site=%s", idx, data["client_id"], site_name)
    return data["client_id"], data["client_token"], {"site": site_name, "hostname": hostname}


async def run_probe(idx: int) -> None:
    backoff = 1.0
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                client_id, token, meta = await register(client, idx)
                break
            except Exception as exc:
                log.warning("Probe %d kunde inte registrera (försöker igen): %s", idx, exc)
                await asyncio.sleep(min(backoff, 30.0))
                backoff *= 2

        headers = {"Authorization": f"Bearer {token}"}
        target = "ad-1.intern"

        while True:
            rtt_min, rtt_avg, rtt_max, loss, success = _sample_rtt()
            payload = {
                "client_id": client_id,
                "results": [
                    {
                        "measurement_id": "builtin-ping-ad",
                        "timestamp": _now_iso(),
                        "type": "icmp_ping",
                        "target": target,
                        "success": success,
                        "rtt_ms_min": rtt_min if success else None,
                        "rtt_ms_avg": rtt_avg if success else None,
                        "rtt_ms_max": rtt_max if success else None,
                        "packet_loss_pct": loss,
                        "site": meta["site"],
                    }
                ],
            }
            try:
                resp = await client.post(
                    f"{COORDINATOR_URL}/probe/results", json=payload, headers=headers
                )
                resp.raise_for_status()
                log.info(
                    "Probe %d (%s) skickade rtt_avg=%.1f loss=%.0f%% success=%s -> %s",
                    idx, meta["site"], rtt_avg, loss, success, resp.json(),
                )
            except Exception as exc:
                log.warning("Probe %d kunde inte rapportera: %s", idx, exc)

            await asyncio.sleep(INTERVAL + random.uniform(-2, 2))


async def main() -> None:
    log.info(
        "Startar mock-klient: %d probes mot %s, interval=%.0fs, scenario=%s",
        MOCK_PROBES, COORDINATOR_URL, INTERVAL, SCENARIO,
    )
    async with asyncio.TaskGroup() as tg:
        for i in range(MOCK_PROBES):
            tg.create_task(run_probe(i))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
