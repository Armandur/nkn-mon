"""NKN-Monitor coordinator – Iteration 1.

Endpoints:
- GET  /healthz          - liveness
- POST /probe/register   - tilldelar fast MVP-token (ingen DB ännu)
- POST /probe/results    - validerar och skriver mätresultat till VictoriaMetrics
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .vm import build_lines, write_to_vm

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("nkn.coordinator")

VM_URL = os.getenv("VICTORIAMETRICS_URL", "http://victoriametrics:8428")
MVP_TOKEN = os.getenv("MVP_TOKEN", "mvp-fixed-token-dev")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=10.0)
    logger.info("Coordinator startar, VM_URL=%s", VM_URL)
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="NKN-Monitor coordinator", version="0.1.0", lifespan=lifespan)


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


class PingResult(BaseModel):
    measurement_id: str
    timestamp: str
    type: Literal["icmp_ping"]
    target: str
    success: bool
    rtt_ms_min: float | None = None
    rtt_ms_avg: float | None = None
    rtt_ms_max: float | None = None
    packet_loss_pct: float | None = None
    site: str | None = None


class ResultsRequest(BaseModel):
    client_id: str
    results: list[PingResult]


class ResultsResponse(BaseModel):
    accepted: int
    rejected: int
    rejected_reasons: list[str] = Field(default_factory=list)


# --- Auth -------------------------------------------------------------------


def _require_bearer(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != MVP_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


# --- Endpoints --------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/probe/register", response_model=RegisterResponse)
async def register(req: RegisterRequest) -> RegisterResponse:
    # Iteration 1: ingen DB-skrivning, ingen riktig validering av registration_key.
    # Token är samma för alla klienter och hårdkodad via env. Detta byts ut i Iteration 2.
    if not req.registration_key:
        raise HTTPException(status_code=400, detail="registration_key krävs")
    client_id = str(uuid.uuid4())
    logger.info(
        "Registrerade klient client_id=%s site=%s host=%s",
        client_id,
        req.client_metadata.site_name,
        req.client_metadata.hostname,
    )
    return RegisterResponse(
        client_id=client_id,
        client_token=MVP_TOKEN,
        initial_spec_url="/probe/spec",
        heartbeat_interval_seconds=300,
    )


@app.post("/probe/results", response_model=ResultsResponse)
async def results(
    req: ResultsRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ResultsResponse:
    _require_bearer(authorization)

    accepted: list[PingResult] = []
    rejected_reasons: list[str] = []
    for r in req.results:
        if not _valid_iso(r.timestamp):
            rejected_reasons.append(f"{r.measurement_id}: ogiltigt timestamp")
            continue
        accepted.append(r)

    if accepted:
        lines = build_lines(req.client_id, accepted)
        try:
            await write_to_vm(request.app.state.http, VM_URL, lines)
        except httpx.HTTPError as exc:
            logger.exception("VM-skrivning misslyckades")
            raise HTTPException(status_code=502, detail=f"VM-skrivning misslyckades: {exc}") from exc

    return ResultsResponse(
        accepted=len(accepted),
        rejected=len(req.results) - len(accepted),
        rejected_reasons=rejected_reasons,
    )


def _valid_iso(ts: str) -> bool:
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        return True
    except (ValueError, TypeError):
        return False
