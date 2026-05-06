"""Skrivning av mätresultat till VictoriaMetrics via Influx line protocol."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import httpx

# Tecken som måste eskapas i tag-nyckel/-värde och field-nyckel
_TAG_ESCAPE = str.maketrans({",": r"\,", "=": r"\=", " ": r"\ "})


def _escape_tag(value: str) -> str:
    return value.translate(_TAG_ESCAPE)


def _iso_to_ns(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _line(metric: str, tags: dict[str, str], value: float, ts_ns: int) -> str:
    tag_str = ",".join(
        f"{_escape_tag(k)}={_escape_tag(v)}"
        for k, v in tags.items()
        if v is not None and v != ""
    )
    prefix = f"{metric},{tag_str}" if tag_str else metric
    return f"{prefix} value={value} {ts_ns}"


def build_lines(client_id: str, results: Iterable) -> list[str]:
    """Bygg Influx line protocol-rader för en sekvens av PingResult-objekt."""
    lines: list[str] = []
    for r in results:
        ts_ns = _iso_to_ns(r.timestamp)
        tags = {
            "client_id": client_id,
            "measurement_id": r.measurement_id,
            "target": r.target,
            "site": r.site or "",
            "target_category": "builtin",
        }
        lines.append(_line("nkn_ping_success", tags, 1.0 if r.success else 0.0, ts_ns))
        if r.rtt_ms_avg is not None:
            lines.append(_line("nkn_ping_rtt_ms", tags, r.rtt_ms_avg, ts_ns))
        if r.rtt_ms_min is not None:
            lines.append(_line("nkn_ping_rtt_ms_min", tags, r.rtt_ms_min, ts_ns))
        if r.rtt_ms_max is not None:
            lines.append(_line("nkn_ping_rtt_ms_max", tags, r.rtt_ms_max, ts_ns))
        if r.packet_loss_pct is not None:
            lines.append(_line("nkn_ping_loss_pct", tags, r.packet_loss_pct, ts_ns))
    return lines


async def write_to_vm(client: httpx.AsyncClient, vm_url: str, lines: list[str]) -> None:
    if not lines:
        return
    body = "\n".join(lines).encode("utf-8")
    resp = await client.post(f"{vm_url.rstrip('/')}/write", content=body)
    resp.raise_for_status()
