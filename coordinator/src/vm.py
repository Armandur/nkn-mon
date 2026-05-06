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


def _common_tags(client_id: str, r) -> dict[str, str]:
    return {
        "client_id": client_id,
        "measurement_id": r.measurement_id,
        "target": r.target,
        "site": r.site or "",
        "target_category": getattr(r, "category", None) or "builtin",
    }


def build_lines(client_id: str, results: Iterable) -> list[str]:
    """Bygg Influx line protocol-rader för en sekvens av MeasurementResult.

    Per typ:
    - icmp_ping  -> nkn_ping_success / nkn_ping_rtt_ms[ _min/_max] / nkn_ping_loss_pct
    - tcp_ping   -> nkn_tcp_success / nkn_tcp_rtt_ms
    - dns_query  -> nkn_dns_query_success / nkn_dns_query_ms / nkn_dns_query_records
    - http_get   -> nkn_http_success / nkn_http_status / nkn_http_response_ms / nkn_http_ttfb_ms
    """
    lines: list[str] = []
    for r in results:
        ts_ns = _iso_to_ns(r.timestamp)
        tags = _common_tags(client_id, r)
        success_value = 1.0 if r.success else 0.0

        if r.type == "icmp_ping":
            lines.append(_line("nkn_ping_success", tags, success_value, ts_ns))
            if r.rtt_ms_avg is not None:
                lines.append(_line("nkn_ping_rtt_ms", tags, r.rtt_ms_avg, ts_ns))
            if r.rtt_ms_min is not None:
                lines.append(_line("nkn_ping_rtt_ms_min", tags, r.rtt_ms_min, ts_ns))
            if r.rtt_ms_max is not None:
                lines.append(_line("nkn_ping_rtt_ms_max", tags, r.rtt_ms_max, ts_ns))
            if r.packet_loss_pct is not None:
                lines.append(_line("nkn_ping_loss_pct", tags, r.packet_loss_pct, ts_ns))

        elif r.type == "tcp_ping":
            lines.append(_line("nkn_tcp_success", tags, success_value, ts_ns))
            if r.rtt_ms is not None:
                lines.append(_line("nkn_tcp_rtt_ms", tags, r.rtt_ms, ts_ns))

        elif r.type == "dns_query":
            lines.append(_line("nkn_dns_query_success", tags, success_value, ts_ns))
            if r.rtt_ms is not None:
                lines.append(_line("nkn_dns_query_ms", tags, r.rtt_ms, ts_ns))
            if r.dns_records is not None:
                lines.append(_line("nkn_dns_query_records", tags, float(r.dns_records), ts_ns))

        elif r.type == "http_get":
            lines.append(_line("nkn_http_success", tags, success_value, ts_ns))
            if r.http_status is not None:
                lines.append(_line("nkn_http_status", tags, float(r.http_status), ts_ns))
            if r.http_total_ms is not None:
                lines.append(_line("nkn_http_response_ms", tags, r.http_total_ms, ts_ns))
            if r.http_ttfb_ms is not None:
                lines.append(_line("nkn_http_ttfb_ms", tags, r.http_ttfb_ms, ts_ns))
    return lines


def build_heartbeat_lines(
    client_id: str,
    site: str,
    classification: str,
    timestamp: str,
    canary_results: list[tuple[str, bool, float | None]],
) -> list[str]:
    """Bygg metrics för en heartbeat: nkn_probe_up + canary-mätningar."""
    ts_ns = _iso_to_ns(timestamp)
    base_tags = {
        "client_id": client_id,
        "site": site,
        "classification": classification,
    }
    lines = [_line("nkn_probe_up", base_tags, 1.0, ts_ns)]
    for target, reachable, rtt_ms in canary_results:
        ctags = {**base_tags, "target": target}
        lines.append(_line("nkn_canary_reachable", ctags, 1.0 if reachable else 0.0, ts_ns))
        if rtt_ms is not None:
            lines.append(_line("nkn_canary_rtt_ms", ctags, rtt_ms, ts_ns))
    return lines


async def write_to_vm(client: httpx.AsyncClient, vm_url: str, lines: list[str]) -> None:
    if not lines:
        return
    body = "\n".join(lines).encode("utf-8")
    resp = await client.post(f"{vm_url.rstrip('/')}/write", content=body)
    resp.raise_for_status()
