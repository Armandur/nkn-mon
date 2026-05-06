"""NKN-Monitor coordinator – Iteration 2 (leverans 1).

Endpoints:
- GET  /healthz
- POST /probe/register   - validerar registration_key, skapar probe, returnerar unik token
- GET  /probe/spec       - returnerar mätspec (kräver bearer-token)
- POST /probe/results    - validerar token, skriver till VictoriaMetrics
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .api.admin import router as admin_router
from .classification import classify_public_ip
from .config import CoordinatorConfig, load_config
from .peers import assign_peers
from .storage.sqlite import Probe, Storage, generate_token, open_storage
from .vm import build_heartbeat_lines, build_lines, write_to_vm

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("nkn.coordinator")

VM_URL = os.getenv("VICTORIAMETRICS_URL", "http://victoriametrics:8428")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=10.0)
    app.state.config = load_config()
    app.state.storage = open_storage()
    logger.info(
        "Coordinator startar, VM_URL=%s, %d builtin-mått, %d registrerade probes",
        VM_URL,
        len(app.state.config.builtin_measurements),
        app.state.storage.count_probes(),
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="NKN-Monitor coordinator", version="0.2.0", lifespan=lifespan)
app.include_router(admin_router)


# --- Modeller ---------------------------------------------------------------


class ClientMetadata(BaseModel):
    hostname: str | None = None
    ecclesiastical_unit: str | None = None
    site_name: str | None = None
    site_type: str | None = None
    contact_person: str | None = None
    notes: str | None = None


class RegisterRequest(BaseModel):
    registration_key: str
    client_metadata: ClientMetadata = Field(default_factory=ClientMetadata)


class RegisterResponse(BaseModel):
    client_id: str
    client_token: str
    initial_spec_url: str
    heartbeat_interval_seconds: int


class SpecMeasurement(BaseModel):
    id: str
    category: Literal["builtin", "peer", "user_defined"] = "builtin"
    type: str
    target: str
    interval_seconds: int
    extra: dict[str, Any] = Field(default_factory=dict)


class CanaryTarget(BaseModel):
    target: str
    description: str | None = None


class SpecResponse(BaseModel):
    spec_version: str
    valid_until: str
    measurements: list[SpecMeasurement]
    canary_targets: list[CanaryTarget] = Field(default_factory=list)


class MeasurementResult(BaseModel):
    """Resultat från en av {icmp_ping, tcp_ping, dns_query, http_get}.

    Per-typ-fält är optional och fylls i bara av relevant mättyp.
    """
    measurement_id: str
    timestamp: str
    type: Literal["icmp_ping", "tcp_ping", "dns_query", "http_get"]
    target: str
    success: bool
    site: str | None = None
    category: str = "builtin"
    peer_site: str | None = None
    sender_local_ip: str | None = None

    # icmp_ping
    rtt_ms_min: float | None = None
    rtt_ms_avg: float | None = None
    rtt_ms_max: float | None = None
    packet_loss_pct: float | None = None

    # tcp_ping & dns_query
    rtt_ms: float | None = None

    # dns_query
    dns_records: int | None = None

    # http_get
    http_status: int | None = None
    http_total_ms: float | None = None
    http_ttfb_ms: float | None = None


class ResultsRequest(BaseModel):
    client_id: str
    results: list[MeasurementResult]


class ResultsResponse(BaseModel):
    accepted: int
    rejected: int
    rejected_reasons: list[str] = Field(default_factory=list)


class CanaryResult(BaseModel):
    target: str
    reachable: bool
    rtt_ms: float | None = None


class NetworkContext(BaseModel):
    public_ip: str | None = None
    local_ipv4: list[str] = Field(default_factory=list)
    default_gateway: str | None = None
    dns_servers: list[str] = Field(default_factory=list)
    domain_membership: str | None = None
    canary_results: list[CanaryResult] = Field(default_factory=list)


class HostInfo(BaseModel):
    os: str | None = None
    uptime_hours: float | None = None


class HeartbeatRequest(BaseModel):
    timestamp: str
    version: str | None = None
    network_context: NetworkContext = Field(default_factory=NetworkContext)
    host_info: HostInfo = Field(default_factory=HostInfo)


class HeartbeatResponse(BaseModel):
    spec_changed: bool = True
    spec_url: str = "/probe/spec"
    network_classification: str
    next_heartbeat_in_seconds: int
    client_actions: list[str] = Field(default_factory=list)


# --- Auth-dependency --------------------------------------------------------


def authenticated_probe(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Probe:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    storage: Storage = request.app.state.storage
    probe = storage.find_probe_by_token(token)
    if probe is None or not probe.enabled:
        raise HTTPException(status_code=401, detail="Invalid or disabled token")
    return probe


# --- Endpoints --------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/probe/register", response_model=RegisterResponse)
async def register(req: RegisterRequest, request: Request) -> RegisterResponse:
    config: CoordinatorConfig = request.app.state.config
    storage: Storage = request.app.state.storage

    if req.registration_key not in config.registration_keys:
        raise HTTPException(status_code=403, detail="Ogiltig registreringsnyckel")

    client_id = str(uuid.uuid4())
    token = generate_token()
    storage.register_probe(
        probe_id=client_id,
        token=token,
        hostname=req.client_metadata.hostname,
        site_name=req.client_metadata.site_name,
        ecclesiastical_unit=req.client_metadata.ecclesiastical_unit,
        site_type=req.client_metadata.site_type,
        notes=req.client_metadata.notes,
    )
    logger.info(
        "Registrerade probe client_id=%s host=%s site=%s",
        client_id,
        req.client_metadata.hostname,
        req.client_metadata.site_name,
    )
    return RegisterResponse(
        client_id=client_id,
        client_token=token,
        initial_spec_url="/probe/spec",
        heartbeat_interval_seconds=config.heartbeat_interval_seconds,
    )


@app.get("/probe/spec", response_model=SpecResponse)
async def get_spec(request: Request, probe: Probe = Depends(authenticated_probe)) -> SpecResponse:
    config: CoordinatorConfig = request.app.state.config
    storage: Storage = request.app.state.storage
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(seconds=config.spec_validity_seconds)
    measurements = [_to_spec_measurement(m) for m in config.builtin_measurements]

    # Lägg till peer-mätningar (Iteration 3): plocka N andra NKN-probes på
    # andra /24 och låt klienten pinga dem över deras lokala IP.
    all_probes = storage.list_probes()
    me = next((p for p in all_probes if p["id"] == probe.id), None)
    if me:
        peers = assign_peers(me, all_probes, count=config.peer_count_per_probe)
        for peer in peers:
            local_ips = peer.get("last_local_ipv4") or []
            if not local_ips:
                continue
            measurements.append(
                SpecMeasurement(
                    id=f"peer-{peer['id'][:8]}",
                    category="peer",
                    type="icmp_ping",
                    target=local_ips[0],
                    interval_seconds=config.peer_interval_seconds,
                    extra={
                        "packet_count": 4,
                        "peer_client_id": peer["id"],
                        "peer_site": peer.get("site_name") or "",
                    },
                )
            )

    canaries = [
        CanaryTarget(target=c.get("target", ""), description=c.get("description"))
        for c in config.canary_targets
        if c.get("target")
    ]
    return SpecResponse(
        spec_version=now.isoformat(timespec="seconds"),
        valid_until=valid_until.isoformat(timespec="seconds"),
        measurements=measurements,
        canary_targets=canaries,
    )


@app.post("/probe/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    req: HeartbeatRequest,
    request: Request,
    probe: Probe = Depends(authenticated_probe),
) -> HeartbeatResponse:
    if not _valid_iso(req.timestamp):
        raise HTTPException(status_code=400, detail="Ogiltigt timestamp")

    config: CoordinatorConfig = request.app.state.config
    storage: Storage = request.app.state.storage

    classification = classify_public_ip(
        req.network_context.public_ip, config.nkn_public_ip_ranges
    )
    storage.update_heartbeat_meta(
        probe_id=probe.id,
        public_ip=req.network_context.public_ip,
        classification=classification,
        version=req.version,
        local_ipv4=list(req.network_context.local_ipv4),
    )

    lines = build_heartbeat_lines(
        client_id=probe.id,
        site=probe.site_name or "",
        classification=classification,
        timestamp=req.timestamp,
        canary_results=[
            (cr.target, cr.reachable, cr.rtt_ms) for cr in req.network_context.canary_results
        ],
    )
    if lines:
        try:
            await write_to_vm(request.app.state.http, VM_URL, lines)
        except httpx.HTTPError as exc:
            logger.warning("Kunde inte skriva heartbeat-metrics: %s", exc)

    logger.info(
        "Heartbeat probe=%s public_ip=%s -> %s",
        probe.id, req.network_context.public_ip, classification,
    )
    return HeartbeatResponse(
        spec_changed=True,
        spec_url="/probe/spec",
        network_classification=classification,
        next_heartbeat_in_seconds=config.heartbeat_interval_seconds,
    )


@app.post("/probe/results", response_model=ResultsResponse)
async def results(
    req: ResultsRequest,
    request: Request,
    probe: Probe = Depends(authenticated_probe),
) -> ResultsResponse:
    if probe.id != req.client_id:
        raise HTTPException(status_code=403, detail="client_id matchar inte token")

    accepted: list[MeasurementResult] = []
    rejected_reasons: list[str] = []
    for r in req.results:
        if not _valid_iso(r.timestamp):
            rejected_reasons.append(f"{r.measurement_id}: ogiltigt timestamp")
            continue
        accepted.append(r)

    if accepted:
        site = next((r.site for r in accepted if r.site), probe.site_name)
        for r in accepted:
            if r.site is None:
                r.site = site
        lines = build_lines(probe.id, accepted)
        try:
            await write_to_vm(request.app.state.http, VM_URL, lines)
        except httpx.HTTPError as exc:
            logger.exception("VM-skrivning misslyckades")
            raise HTTPException(status_code=502, detail=f"VM-skrivning misslyckades: {exc}") from exc

    request.app.state.storage.touch_heartbeat(probe.id)

    return ResultsResponse(
        accepted=len(accepted),
        rejected=len(req.results) - len(accepted),
        rejected_reasons=rejected_reasons,
    )


# --- Hjälpare ---------------------------------------------------------------


def _to_spec_measurement(raw: dict[str, Any]) -> SpecMeasurement:
    known = {"id", "type", "target", "interval_seconds", "category"}
    extra = {k: v for k, v in raw.items() if k not in known}
    return SpecMeasurement(
        id=raw["id"],
        category=raw.get("category", "builtin"),
        type=raw["type"],
        target=raw["target"],
        interval_seconds=int(raw.get("interval_seconds", 60)),
        extra=extra,
    )


def _valid_iso(ts: str) -> bool:
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        return True
    except (ValueError, TypeError):
        return False
