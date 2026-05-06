"""Admin-UI för coordinator-config med hot-reload.

Säkerhet:
- Skyddat med HTTP Basic (env ADMIN_USER + ADMIN_TOKEN, default admin/admin-dev).
- Loggar varning om defaults används.
- Får aldrig exponeras publikt utan att ADMIN_TOKEN bytts ut. Caddy bör
  begränsa åtkomsten till /admin till specifika nät i prod.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..config import CoordinatorConfig

VM_URL = os.getenv("VICTORIAMETRICS_URL", "http://victoriametrics:8428")

logger = logging.getLogger("nkn.admin")
router = APIRouter(prefix="/admin", tags=["admin"])
_security = HTTPBasic()

_ADMIN_USER = os.getenv("ADMIN_USER", "admin")
_ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin-dev")
if _ADMIN_TOKEN == "admin-dev":
    logger.warning("ADMIN_TOKEN är default 'admin-dev' - byt innan exponering utanför Tailscale")


def _config_path() -> Path:
    return Path(os.getenv("COORDINATOR_CONFIG", "/app/config.yaml"))


def require_admin(creds: HTTPBasicCredentials = Depends(_security)) -> str:
    user_ok = secrets.compare_digest(creds.username, _ADMIN_USER)
    token_ok = secrets.compare_digest(creds.password, _ADMIN_TOKEN)
    if not (user_ok and token_ok):
        raise HTTPException(
            status_code=401,
            detail="Auth required",
            headers={"WWW-Authenticate": 'Basic realm="NKN-Monitor admin"'},
        )
    return creds.username


@router.get("/", response_class=HTMLResponse)
def admin_index(_: str = Depends(require_admin)) -> str:
    return _ADMIN_HTML


@router.get("/api/config", response_class=PlainTextResponse)
def get_config(_: str = Depends(require_admin)) -> str:
    return _config_path().read_text(encoding="utf-8")


@router.get("/api/config.json")
def get_config_json(_: str = Depends(require_admin)) -> dict:
    """Strukturerat config-objekt för formulär-UI:t."""
    raw = yaml.safe_load(_config_path().read_text(encoding="utf-8")) or {}
    return {
        "heartbeat_interval_seconds": int(raw.get("heartbeat_interval_seconds", 300)),
        "spec_validity_seconds": int(raw.get("spec_validity_seconds", 3600)),
        "peer_count_per_probe": int(raw.get("peer_count_per_probe", 3)),
        "peer_interval_seconds": int(raw.get("peer_interval_seconds", 300)),
        "registration_keys": list(raw.get("registration_keys", [])),
        "nkn_public_ip_ranges": list(raw.get("nkn_public_ip_ranges", [])),
        "canary_targets": list(raw.get("canary_targets", [])),
        "builtin_measurements": list(raw.get("builtin_measurements", [])),
    }


@router.put("/api/config.json")
async def put_config_json(request: Request, _: str = Depends(require_admin)) -> dict:
    """Tar emot strukturerat config-objekt, validerar och serialiserar till YAML."""
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload måste vara ett objekt")

    try:
        new_cfg = CoordinatorConfig(
            heartbeat_interval_seconds=int(payload.get("heartbeat_interval_seconds", 300)),
            spec_validity_seconds=int(payload.get("spec_validity_seconds", 3600)),
            registration_keys=list(payload.get("registration_keys", [])),
            builtin_measurements=list(payload.get("builtin_measurements", [])),
            canary_targets=list(payload.get("canary_targets", [])),
            nkn_public_ip_ranges=list(payload.get("nkn_public_ip_ranges", [])),
            peer_count_per_probe=int(payload.get("peer_count_per_probe", 3)),
            peer_interval_seconds=int(payload.get("peer_interval_seconds", 300)),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Ogiltig config: {exc}") from exc

    if not new_cfg.registration_keys:
        raise HTTPException(status_code=400, detail="Minst en registration_key krävs")
    for m in new_cfg.builtin_measurements:
        if "id" not in m or "type" not in m or "target" not in m:
            raise HTTPException(
                status_code=400, detail=f"Måttet saknar id/type/target: {m}"
            )

    # Serialisera tillbaka till YAML (utan kommentarer - rå-fliken bevarar dem)
    yaml_text = yaml.safe_dump(
        {
            "heartbeat_interval_seconds": new_cfg.heartbeat_interval_seconds,
            "spec_validity_seconds": new_cfg.spec_validity_seconds,
            "peer_count_per_probe": new_cfg.peer_count_per_probe,
            "peer_interval_seconds": new_cfg.peer_interval_seconds,
            "registration_keys": new_cfg.registration_keys,
            "builtin_measurements": new_cfg.builtin_measurements,
            "canary_targets": new_cfg.canary_targets,
            "nkn_public_ip_ranges": new_cfg.nkn_public_ip_ranges,
        },
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )

    _config_path().write_text(yaml_text, encoding="utf-8")
    request.app.state.config = new_cfg
    request.app.state.config_reloaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger.info(
        "Config uppdaterad via formulär: %d mått, %d reg-nycklar",
        len(new_cfg.builtin_measurements),
        len(new_cfg.registration_keys),
    )
    return {
        "status": "ok",
        "measurements": len(new_cfg.builtin_measurements),
        "registration_keys": len(new_cfg.registration_keys),
        "reloaded_at": request.app.state.config_reloaded_at,
    }


@router.put("/api/config")
async def put_config(request: Request, _: str = Depends(require_admin)) -> dict:
    raw = (await request.body()).decode("utf-8")
    try:
        parsed = yaml.safe_load(raw)
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError("config måste vara ett YAML-objekt på toppnivå")
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"YAML-fel: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Validera genom att konstruera dataclass utifrån parsed
    try:
        new_cfg = CoordinatorConfig(
            heartbeat_interval_seconds=int(parsed.get("heartbeat_interval_seconds", 300)),
            spec_validity_seconds=int(parsed.get("spec_validity_seconds", 3600)),
            registration_keys=list(parsed.get("registration_keys", [])),
            builtin_measurements=list(parsed.get("builtin_measurements", [])),
            canary_targets=list(parsed.get("canary_targets", [])),
            nkn_public_ip_ranges=list(parsed.get("nkn_public_ip_ranges", [])),
            peer_count_per_probe=int(parsed.get("peer_count_per_probe", 3)),
            peer_interval_seconds=int(parsed.get("peer_interval_seconds", 300)),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Ogiltig config: {exc}") from exc

    if not new_cfg.registration_keys:
        raise HTTPException(status_code=400, detail="Minst en registration_key krävs")
    for m in new_cfg.builtin_measurements:
        if "id" not in m or "type" not in m or "target" not in m:
            raise HTTPException(
                status_code=400,
                detail=f"Måttet saknar id/type/target: {m}",
            )

    # Direktskrivning - tmp+rename funkar inte över Docker bind-mount.
    # Validering ovan har redan parsat YAML, så filen blir aldrig korrupt
    # om inte processen dör mitt i write_text (försumbar risk för en YAML-fil).
    path = _config_path()
    path.write_text(raw, encoding="utf-8")

    request.app.state.config = new_cfg
    request.app.state.config_reloaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger.info(
        "Config uppdaterad: %d mått, %d reg-nycklar",
        len(new_cfg.builtin_measurements),
        len(new_cfg.registration_keys),
    )
    return {
        "status": "ok",
        "measurements": len(new_cfg.builtin_measurements),
        "registration_keys": len(new_cfg.registration_keys),
        "reloaded_at": request.app.state.config_reloaded_at,
    }


@router.get("/api/status")
def get_status(request: Request, _: str = Depends(require_admin)) -> dict:
    cfg: CoordinatorConfig = request.app.state.config
    storage = request.app.state.storage
    return {
        "probes": storage.count_probes(),
        "measurements": len(cfg.builtin_measurements),
        "registration_keys": len(cfg.registration_keys),
        "canary_targets": len(cfg.canary_targets),
        "heartbeat_interval_seconds": cfg.heartbeat_interval_seconds,
        "spec_validity_seconds": cfg.spec_validity_seconds,
        "config_reloaded_at": getattr(request.app.state, "config_reloaded_at", None),
    }


@router.get("/api/probes")
def get_probes(request: Request, _: str = Depends(require_admin)) -> dict:
    return {"probes": request.app.state.storage.list_probes()}


@router.post("/api/probes/sweep")
def sweep_dead_probes(
    request: Request,
    older_than_hours: int = 24,
    _: str = Depends(require_admin),
) -> dict:
    if older_than_hours < 1:
        raise HTTPException(status_code=400, detail="older_than_hours måste vara >= 1")
    deleted = request.app.state.storage.delete_dead_probes(older_than_hours)
    logger.info("Sweep tog bort %d probes äldre än %d h", deleted, older_than_hours)
    return {"deleted": deleted, "older_than_hours": older_than_hours}


@router.get("/api/peer-graph/nodes")
def get_graph_nodes(
    request: Request,
    active_within_minutes: int = 5,
    _: str = Depends(require_admin),
) -> list[dict]:
    """Nodes-format för Grafana Node Graph-panel.

    Krav-fält: id, title. Optional: subTitle, mainStat, color.
    Filtrerar bort probes som inte heartbeatat på active_within_minutes
    så grafen inte överöses med spöken från tidigare körningar.
    """
    from datetime import timedelta
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=active_within_minutes)).isoformat()
    probes = request.app.state.storage.list_probes()
    nodes: list[dict] = []
    for p in probes:
        if not p.get("enabled", True):
            continue
        if p.get("last_classification") != "nkn":
            continue
        hb = p.get("last_heartbeat_at")
        if not hb or hb < threshold:
            continue
        role = p.get("role", "probe")
        ip = (p.get("last_local_ipv4") or [""])[0] or ""
        nodes.append({
            "id": p["id"],
            "title": p.get("site_name") or p["id"][:8],
            "subtitle": role,
            "mainstat": ip,
            "secondarystat": p.get("hostname") or "",
            "color": "blue" if role == "anchor" else "green",
            # nodeRadius är ett valfritt fält som överstyr panelens default
            "nodeRadius": 60 if role == "anchor" else 35,
        })
    return nodes


@router.get("/api/peer-graph/edges")
async def get_graph_edges(
    request: Request,
    active_within_minutes: int = 5,
    _: str = Depends(require_admin),
) -> list[dict]:
    """Edges-format för Grafana Node Graph-panel.

    KRITISKT: alla source och target i edges MÅSTE finnas i nodes-tabellen
    annars kraschar Grafana Node Graph-panelen ('Cannot read properties of
    undefined (reading nodeRadius)'). Filtrerar därför edges till bara de
    där båda noderna fortfarande är aktiva enligt samma kriterier som
    /nodes-endpointen.
    """
    from datetime import timedelta

    storage = request.app.state.storage
    probes = storage.list_probes()
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=active_within_minutes)).isoformat()

    active_ids: set[str] = set()
    site_to_id: dict[str, str] = {}
    for p in probes:
        if not p.get("enabled", True):
            continue
        if p.get("last_classification") != "nkn":
            continue
        hb = p.get("last_heartbeat_at")
        if not hb or hb < threshold:
            continue
        active_ids.add(p["id"])
        if p.get("site_name"):
            site_to_id[p["site_name"]] = p["id"]

    query = 'last_over_time(nkn_ping_rtt_ms{target_category="peer",peer_site!=""}[15m])'
    try:
        resp = await request.app.state.http.get(
            f"{VM_URL}/api/v1/query", params={"query": query}, timeout=5.0
        )
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Kunde inte hämta peer-edges från VM: %s", exc)
        result = []

    edges: dict[tuple[str, str], dict] = {}
    for serie in result:
        m = serie.get("metric", {})
        source = m.get("client_id")
        peer_site = m.get("peer_site")
        if not source or not peer_site:
            continue
        target_id = site_to_id.get(peer_site)
        if not target_id or target_id == source:
            continue
        # Endast edges där BÅDA noderna är aktiva i nodes-listan.
        if source not in active_ids or target_id not in active_ids:
            continue
        try:
            rtt = float(serie["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        key = (source, target_id)
        existing = edges.get(key)
        if existing is None or rtt < existing["_rtt"]:
            if rtt < 30:
                color = "green"
            elif rtt < 80:
                color = "#d7ba7d"  # gul
            elif rtt < 200:
                color = "orange"
            else:
                color = "red"
            edges[key] = {
                "id": f"{source}-{target_id}",
                "source": source,
                "target": target_id,
                "mainstat": f"{rtt:.0f} ms",
                "color": color,
                "thickness": 2 if rtt < 50 else 1,
                "_rtt": rtt,
            }
    out: list[dict] = []
    for e in edges.values():
        e.pop("_rtt", None)
        out.append(e)
    return out


def _build_traceroute_graph(
    storage, site_filter: set[str] | None = None
) -> tuple[dict, dict]:
    """Bygger nodes + edges från senaste traceroute-paths.

    site_filter: om satt, inkludera bara probes vars site_name finns i set:et.
    """
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}

    for pair in storage.list_traceroute_pairs():
        probe_id = pair["client_id"]
        site = pair.get("site_name") or probe_id[:8]
        if site_filter is not None and site not in site_filter:
            continue
        if probe_id not in nodes:
            nodes[probe_id] = {
                "id": probe_id,
                "title": site,
                "subtitle": "probe",
                "mainstat": pair.get("hostname") or "",
                "color": "green",
                "nodeRadius": 50,
            }

        paths = storage.get_traceroute_paths(probe_id, pair["measurement_id"], limit=1)
        if not paths or not paths[0].get("path"):
            continue
        hops = paths[0]["path"]
        if not hops:
            continue

        for ip in hops:
            hop_id = f"hop:{ip}"
            if hop_id not in nodes:
                nodes[hop_id] = {
                    "id": hop_id,
                    "title": ip,
                    "subtitle": "hop",
                    "mainstat": "",
                    "color": "#7a8088",
                    "nodeRadius": 22,
                }

        # Markera sista hop som destination (lila/cyan)
        last_id = f"hop:{hops[-1]}"
        if last_id in nodes and nodes[last_id]["subtitle"] == "hop":
            nodes[last_id]["subtitle"] = "dest"
            nodes[last_id]["color"] = "#a0b5ff"
            nodes[last_id]["nodeRadius"] = 30

        prev = probe_id
        for idx, ip in enumerate(hops):
            curr = f"hop:{ip}"
            key = (prev, curr)
            edges.setdefault(key, {
                "id": f"{prev}->{curr}",
                "source": prev,
                "target": curr,
                "mainstat": str(idx + 1),
            })
            prev = curr

    return nodes, edges


def _parse_site_filter(site: str) -> set[str] | None:
    """Comma-separated sitenamn -> set, tom/wildcard -> None (= alla)."""
    if not site or site.strip() in {"", "*", ".*", "All", "$__all"}:
        return None
    parts = {c.strip() for c in site.split(",") if c.strip() and c.strip() not in {"*", ".*"}}
    return parts or None


@router.get("/api/traceroute-graph/sites")
def list_traceroute_graph_sites(request: Request, _: str = Depends(require_admin)) -> list[dict]:
    """Lista unika site-namn som har traceroute-data, för Grafana variable."""
    storage = request.app.state.storage
    sites = sorted({
        pair.get("site_name") or pair["client_id"][:8]
        for pair in storage.list_traceroute_pairs()
    })
    return [{"site": s} for s in sites]


def _apply_path_hosts_to_nodes(storage, nodes: dict, site_filter: set[str] | None) -> None:
    """Berika hop-noder med hostname som klienten levererat (NKN-internt PTR).

    Klienten gör Resolve-DnsName i sitt eget nätverk så även rent interna
    NKN-namn som inte finns i publik DNS kommer fram. Coordinator gör
    INTE egna DNS-uppslag (skulle missa interna namn om hen ligger externt).
    """
    for pair in storage.list_traceroute_pairs():
        site = pair.get("site_name") or pair["client_id"][:8]
        if site_filter is not None and site not in site_filter:
            continue
        paths = storage.get_traceroute_paths(pair["client_id"], pair["measurement_id"], limit=1)
        if not paths:
            continue
        latest = paths[0]
        hosts = latest.get("path_hosts") or []
        for ip, host in zip(latest.get("path") or [], hosts):
            if not host:
                continue
            nid = f"hop:{ip}"
            n = nodes.get(nid)
            if n and n.get("title") == ip:
                n["title"] = host
                n["mainstat"] = ip


@router.get("/api/traceroute-graph/nodes")
def get_traceroute_graph_nodes(
    request: Request,
    site: str = "",
    _: str = Depends(require_admin),
) -> list[dict]:
    site_filter = _parse_site_filter(site)
    nodes, _ = _build_traceroute_graph(request.app.state.storage, site_filter=site_filter)
    _apply_path_hosts_to_nodes(request.app.state.storage, nodes, site_filter)
    return list(nodes.values())


@router.get("/api/traceroute-graph/edges")
def get_traceroute_graph_edges(
    request: Request,
    site: str = "",
    _: str = Depends(require_admin),
) -> list[dict]:
    nodes, edges = _build_traceroute_graph(
        request.app.state.storage, site_filter=_parse_site_filter(site)
    )
    valid_ids = set(nodes.keys())
    return [e for e in edges.values() if e["source"] in valid_ids and e["target"] in valid_ids]


@router.get("/api/traceroute")
def list_traceroutes(request: Request, _: str = Depends(require_admin)) -> dict:
    """Lista alla (probe, mått) som har traceroute-data, med senaste hops/timestamp."""
    return {"items": request.app.state.storage.list_traceroute_pairs()}


@router.get("/api/traceroute/{client_id}/{measurement_id}")
def get_traceroute_history(
    client_id: str,
    measurement_id: str,
    request: Request,
    limit: int = 20,
    _: str = Depends(require_admin),
) -> dict:
    paths = request.app.state.storage.get_traceroute_paths(client_id, measurement_id, limit=limit)
    return {
        "client_id": client_id,
        "measurement_id": measurement_id,
        "paths": paths,
    }


@router.post("/api/probes/{probe_id}/role")
def set_probe_role(
    probe_id: str,
    role: str,
    request: Request,
    _: str = Depends(require_admin),
) -> dict:
    try:
        ok = request.app.state.storage.set_probe_role(probe_id, role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Probe finns inte")
    logger.info("Probe %s satt till role=%s", probe_id, role)
    return {"id": probe_id, "role": role}


_ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<title>NKN-Monitor admin</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0f1419;
    --surface: #1a1f26;
    --border: #2a3038;
    --text: #d4d8de;
    --muted: #7a8088;
    --accent: #4ec9b0;
    --accent-dim: #2d6e63;
    --warn: #d7ba7d;
    --error: #f48771;
    --success: #6a9955;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh;
  }
  header {
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: baseline; gap: 16px;
  }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header .subtitle { color: var(--muted); font-size: 13px; }
  main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  .grid {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 24px;
  }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
  }
  .card h2 {
    margin: 0 0 12px 0;
    font-size: 14px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--muted);
  }
  textarea#editor {
    width: 100%; min-height: 540px;
    padding: 12px;
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 13px; line-height: 1.5;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 4px;
    resize: vertical;
    tab-size: 2;
  }
  textarea#editor:focus { outline: 1px solid var(--accent); border-color: var(--accent); }
  .toolbar {
    display: flex; gap: 8px; align-items: center;
    margin-top: 12px;
  }
  button {
    background: var(--accent); color: var(--bg);
    border: none; border-radius: 4px;
    padding: 8px 16px;
    font-size: 13px; font-weight: 600;
    cursor: pointer;
  }
  button:hover { background: #5fdbc1; }
  button:disabled { background: var(--accent-dim); cursor: not-allowed; }
  button.secondary {
    background: transparent; color: var(--text);
    border: 1px solid var(--border);
  }
  button.secondary:hover { background: var(--surface); }
  .status {
    margin-left: auto;
    font-size: 13px; color: var(--muted);
  }
  .status.success { color: var(--success); }
  .status.error { color: var(--error); }
  dl.stats { margin: 0; }
  dl.stats div { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border); }
  dl.stats div:last-child { border-bottom: none; }
  dl.stats dt { color: var(--muted); font-size: 13px; }
  dl.stats dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
  .hint {
    font-size: 12px; color: var(--muted);
    margin-top: 8px;
  }
  kbd {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 3px; padding: 1px 6px;
    font-family: inherit; font-size: 12px;
  }
  .probes-section { margin-top: 24px; }
  table.probes {
    width: 100%; border-collapse: collapse; font-size: 13px;
    font-variant-numeric: tabular-nums;
  }
  table.probes th, table.probes td {
    padding: 6px 8px; text-align: left;
    border-bottom: 1px solid var(--border);
  }
  table.probes th {
    color: var(--muted); font-weight: 500;
    text-transform: uppercase; font-size: 11px;
    letter-spacing: 0.05em;
  }
  table.probes td.muted { color: var(--muted); }
  .badge {
    display: inline-block; padding: 1px 8px; border-radius: 3px;
    font-size: 11px; font-weight: 600; text-transform: uppercase;
  }
  .badge.nkn { background: var(--accent-dim); color: var(--accent); }
  .badge.external { background: rgba(215, 186, 125, 0.2); color: var(--warn); }
  .badge.unknown { background: rgba(122, 128, 136, 0.2); color: var(--muted); }
  .badge.anchor { background: rgba(92, 109, 209, 0.25); color: #a0b5ff; }
  .badge.probe { background: transparent; color: var(--muted); border: 1px solid var(--border); }
  .role-select {
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 3px;
    padding: 1px 4px; font-size: 12px; font-family: inherit;
  }
  .age-fresh { color: var(--success); }
  .age-stale { color: var(--warn); }
  .age-dead { color: var(--error); }
  .tabs {
    display: flex; gap: 4px;
    border-bottom: 1px solid var(--border);
    margin: -4px -16px 16px;
    padding: 0 16px;
  }
  .tab {
    padding: 8px 14px;
    background: transparent; border: none;
    color: var(--muted); cursor: pointer;
    font-size: 13px; font-weight: 500;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
  }
  .tab:hover { color: var(--text); }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  /* Generisk form-styling: samma utseende överallt */
  input[type="text"], input[type="number"], input[type="password"], select {
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 3px;
    padding: 6px 8px; font-size: 13px; font-family: inherit;
    box-sizing: border-box;
  }
  input[type="text"]:focus, input[type="number"]:focus, select:focus {
    outline: 1px solid var(--accent); border-color: var(--accent);
  }
  input[type="checkbox"] {
    accent-color: var(--accent);
    width: 16px; height: 16px;
    margin: 4px 0;
  }
  label.field, .form-grid label, .meas-fields label {
    display: flex; flex-direction: column; gap: 4px;
    font-size: 12px; color: var(--muted);
  }
  label.field-inline {
    display: flex; flex-direction: row; gap: 8px; align-items: center;
    font-size: 13px; color: var(--text);
  }
  .form-section { margin-bottom: 24px; }
  .form-section h3 {
    margin: 0 0 8px 0; font-size: 13px; font-weight: 600;
    color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;
  }
  .form-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
  }
  .list-row {
    display: flex; gap: 8px; align-items: center;
    margin-bottom: 6px;
  }
  .list-row input { flex: 1; }
  .list-row button { padding: 6px 10px; font-size: 12px; }
  .list-add {
    background: transparent; color: var(--accent);
    border: 1px dashed var(--border); border-radius: 3px;
    padding: 6px 12px; font-size: 12px; font-weight: 500;
    cursor: pointer; font-family: inherit;
  }
  .list-add:hover { border-color: var(--accent); background: var(--surface); }
  .meas-card {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 4px; padding: 12px; margin-bottom: 8px;
  }
  .meas-card-head {
    display: flex; gap: 8px; align-items: center;
    margin-bottom: 8px;
  }
  .meas-card-head .meas-id {
    flex: 1; font-weight: 600;
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 13px;
  }
  .meas-card-head .meas-type {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 3px; padding: 2px 8px;
    font-size: 11px; text-transform: uppercase;
    color: var(--accent);
  }
  .meas-fields {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
  }
  .toolbar-inline {
    display: flex; align-items: center; gap: 12px;
    flex-wrap: wrap;
  }
  .probes-header {
    display: flex; align-items: center; gap: 16px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .probes-header h2 { margin: 0; flex: 1; }
  .num-narrow { width: 70px; }
  .help-text {
    color: var(--muted); font-size: 12px;
    margin: 0 0 12px 0; line-height: 1.5;
    max-width: 80ch;
  }
  /* Tabb-paneler ska inte ha extra padding mot card-edges */
  .tab-panel textarea#editor { margin-top: 0; }
  .tr-row { cursor: pointer; }
  .tr-row:hover { background: rgba(255,255,255,0.03); }
  .tr-detail {
    background: var(--bg);
    padding: 12px 16px;
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 12px; line-height: 1.6;
    border-bottom: 1px solid var(--border);
  }
  .tr-hop { display: flex; gap: 12px; }
  .tr-hop-num { color: var(--muted); width: 24px; text-align: right; }
  .tr-hop-ip { color: var(--accent); }
  .tr-history-line {
    color: var(--muted); font-size: 11px;
    margin-top: 8px; padding-top: 8px;
    border-top: 1px dashed var(--border);
  }
</style>
</head>
<body>
<header>
  <h1>NKN-Monitor admin</h1>
  <span class="subtitle">config.yaml hot-reload</span>
</header>
<main>
  <div class="grid">
    <section class="card">
      <h2>Konfiguration</h2>
      <div class="tabs">
        <button class="tab active" data-tab="form">Formulär</button>
        <button class="tab" data-tab="yaml">YAML (rå)</button>
      </div>

      <div class="tab-panel active" id="tab-form">
        <div class="form-section">
          <h3>Globala värden</h3>
          <p class="help-text">
            Heartbeat-intervall styr hur ofta klienter ska skicka in network context check.
            Spec-giltighet är hur länge en utlevererad mätspec gäller; klienter hämtar ny spec efter denna tid.
            För dev: håll lågt (60-120 s) så config-ändringar slår igenom snabbt. För prod: höj till några minuter eller mer.
            Peer-värden styr peer-mätningen (Iteration 3): coordinator tilldelar varje probe N andra probes på olika /24 att mäta mot, roterar dagligen.
          </p>
          <div class="form-grid">
            <label>Heartbeat-intervall (s)<input type="number" id="f-heartbeat" min="30"></label>
            <label>Spec-giltighet (s)<input type="number" id="f-spec-validity" min="30"></label>
            <label>Peers per probe<input type="number" id="f-peer-count" min="0" max="10"></label>
            <label>Peer-mätintervall (s)<input type="number" id="f-peer-interval" min="60"></label>
          </div>
        </div>

        <div class="form-section">
          <h3>Registreringsnycklar</h3>
          <p class="help-text">
            Tillåtna registreringsnycklar. Probes använder en av dessa vid första
            <code>/probe/register</code>. Rotera vid behov - gamla nycklar kan tas bort
            utan att redan registrerade probes påverkas, eftersom de därefter använder
            sin individuella client_token.
          </p>
          <div id="f-reg-keys"></div>
          <button type="button" class="list-add" data-add="reg-keys">+ lägg till nyckel</button>
        </div>

        <div class="form-section">
          <h3>NKN publika IP-ranges (CIDR)</h3>
          <p class="help-text">
            IP-ranges som klassificeras som NKN. En probe vars publika IP ligger inom
            något av dessa CIDR-block taggas som <code>nkn</code> i metrics; övriga blir
            <code>external</code>. Justera när du vet exakt vilka ranges Global Connect
            tilldelat NKN.
          </p>
          <div id="f-nkn-ranges"></div>
          <button type="button" class="list-add" data-add="nkn-ranges">+ lägg till range</button>
        </div>

        <div class="form-section">
          <h3>Canary-mål</h3>
          <p class="help-text">
            Canary-mål används av klientens network context check. Klienten pingar
            dessa vid varje heartbeat och rapporterar reachability + RTT, så att
            coordinator vet att proben kan nå sina lokala referenspunkter (AD, SMTP osv).
          </p>
          <div id="f-canary"></div>
          <button type="button" class="list-add" data-add="canary">+ lägg till canary</button>
        </div>

        <div class="form-section">
          <h3>Builtin-mätningar</h3>
          <p class="help-text">
            Mål som distribueras till alla probes. Varje <code>id</code> måste vara unikt.
            Klienten utför mätningar enligt typ:
            <code>icmp_ping</code> (Test-Connection),
            <code>tcp_ping</code> (TcpClient),
            <code>dns_query</code> (Resolve-DnsName mot specifik DNS-server),
            <code>http_get</code> (Invoke-WebRequest),
            <code>traceroute</code> (Test-NetConnection -TraceRoute, inkl. reverse-DNS för hops).
          </p>
          <div id="f-measurements"></div>
          <button type="button" class="list-add" data-add="measurement">+ lägg till mätning</button>
        </div>

        <div class="toolbar">
          <button id="form-save">Spara &amp; reload</button>
          <button class="secondary" id="form-reload">Hämta från disk</button>
          <span id="form-status" class="status">Laddar…</span>
        </div>
      </div>

      <div class="tab-panel" id="tab-yaml">
        <textarea id="editor" spellcheck="false" autocomplete="off"></textarea>
        <div class="toolbar">
          <button id="save">Spara &amp; reload</button>
          <button class="secondary" id="reload">Hämta från disk</button>
          <span id="status" class="status">Laddar…</span>
        </div>
        <p class="hint"><kbd>Ctrl</kbd>+<kbd>S</kbd> sparar. Bevarar kommentarer. Formulär-fliken serialiserar tillbaka utan kommentarer.</p>
      </div>
    </section>
    <aside class="card">
      <h2>Status</h2>
      <dl class="stats" id="stats">
        <div><dt>Registrerade probes</dt><dd id="s-probes">-</dd></div>
        <div><dt>Builtin-mått</dt><dd id="s-measurements">-</dd></div>
        <div><dt>Registreringsnycklar</dt><dd id="s-keys">-</dd></div>
        <div><dt>Canary-mål</dt><dd id="s-canary">-</dd></div>
        <div><dt>Heartbeat-intervall</dt><dd id="s-hb">-</dd></div>
        <div><dt>Senaste reload</dt><dd id="s-reload">-</dd></div>
      </dl>
    </aside>
  </div>

  <section class="card probes-section">
    <div class="probes-header">
      <h2>Registrerade probes</h2>
      <div class="toolbar-inline">
        <label class="field-inline">
          Rensa probes äldre än
          <input id="sweep-hours" type="number" min="1" value="24" class="num-narrow"> h
        </label>
        <button class="secondary" id="sweep-btn">Rensa döda</button>
      </div>
    </div>
    <table class="probes">
      <thead>
        <tr>
          <th>Site</th>
          <th>Hostname</th>
          <th>Lokal IP</th>
          <th>Roll</th>
          <th>Klassificering</th>
          <th>Publik IP</th>
          <th>Senaste heartbeat</th>
          <th>Version</th>
        </tr>
      </thead>
      <tbody id="probes-body"></tbody>
    </table>
  </section>

  <section class="card probes-section">
    <h2>Senaste traceroute</h2>
    <p class="hint">Klicka på en rad för att se hops och historik (senaste 20 körningar).</p>
    <table class="probes">
      <thead>
        <tr>
          <th>Site</th>
          <th>Mätning</th>
          <th>Mål</th>
          <th>Hops</th>
          <th>Total RTT</th>
          <th>När</th>
        </tr>
      </thead>
      <tbody id="traceroute-body"></tbody>
    </table>
  </section>
</main>
<script>
const editor = document.getElementById("editor");
const statusEl = document.getElementById("status");
const saveBtn = document.getElementById("save");
const reloadBtn = document.getElementById("reload");

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

async function loadConfig() {
  setStatus("Hämtar…");
  const r = await fetch("/admin/api/config");
  if (!r.ok) { setStatus("Fel: " + r.status, "error"); return; }
  editor.value = await r.text();
  setStatus("Laddad", "success");
  refreshStatus();
}

async function refreshStatus() {
  const r = await fetch("/admin/api/status");
  if (!r.ok) return;
  const s = await r.json();
  document.getElementById("s-probes").textContent = s.probes;
  document.getElementById("s-measurements").textContent = s.measurements;
  document.getElementById("s-keys").textContent = s.registration_keys;
  document.getElementById("s-canary").textContent = s.canary_targets;
  document.getElementById("s-hb").textContent = s.heartbeat_interval_seconds + "s";
  document.getElementById("s-reload").textContent = s.config_reloaded_at || "(uppstart)";
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

function formatAge(iso) {
  if (!iso) return { text: "aldrig", cls: "age-dead" };
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  let text;
  if (seconds < 30) {
    text = "just nu";
  } else if (seconds < 60) {
    text = `för ${seconds} sekunder sen`;
  } else if (seconds < 3600) {
    const m = Math.round(seconds / 60);
    text = `för ${m} ${m === 1 ? "minut" : "minuter"} sen`;
  } else if (seconds < 86400) {
    const h = Math.round(seconds / 3600);
    text = `för ${h} ${h === 1 ? "timme" : "timmar"} sen`;
  } else {
    const d = Math.round(seconds / 86400);
    text = `för ${d} ${d === 1 ? "dag" : "dagar"} sen`;
  }
  let cls = "age-fresh";
  if (seconds > 600) cls = "age-stale";
  if (seconds > 3600) cls = "age-dead";
  return { text, cls };
}

async function refreshProbes() {
  const r = await fetch("/admin/api/probes");
  if (!r.ok) return;
  const data = await r.json();
  const tbody = document.getElementById("probes-body");
  if (!data.probes.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="muted">Inga registrerade probes ännu</td></tr>';
    return;
  }
  tbody.innerHTML = data.probes.map(p => {
    const cls = (p.last_classification || "unknown").toLowerCase();
    const age = formatAge(p.last_heartbeat_at);
    const role = p.role || "probe";
    const localIps = (p.last_local_ipv4 || []);
    const localIpHtml = localIps.length === 0
      ? '<span class="muted">-</span>'
      : escapeHtml(localIps[0]) + (localIps.length > 1 ? ` <span class="muted">+${localIps.length-1}</span>` : "");
    const roleSelect = `<select class="role-select" data-id="${escapeHtml(p.id)}" data-current="${role}">
      <option value="probe"${role === "probe" ? " selected" : ""}>probe</option>
      <option value="anchor"${role === "anchor" ? " selected" : ""}>anchor</option>
    </select>`;
    return `<tr>
      <td>${escapeHtml(p.site_name) || '<span class="muted">-</span>'}</td>
      <td>${escapeHtml(p.hostname) || '<span class="muted">-</span>'}</td>
      <td class="muted" title="${escapeHtml(localIps.join(', '))}">${localIpHtml}</td>
      <td>${roleSelect}</td>
      <td><span class="badge ${cls}">${escapeHtml(cls)}</span></td>
      <td>${escapeHtml(p.last_seen_public_ip) || '<span class="muted">-</span>'}</td>
      <td class="${age.cls}">${age.text}</td>
      <td class="muted">${escapeHtml(p.version) || '-'}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll(".role-select").forEach(sel => {
    sel.addEventListener("change", async (e) => {
      const id = e.target.dataset.id;
      const newRole = e.target.value;
      const r = await fetch(`/admin/api/probes/${id}/role?role=${newRole}`, { method: "POST" });
      if (r.ok) {
        setStatus(`Probe ${id.slice(0,8)} -> ${newRole}`, "success");
        refreshProbes();
      } else {
        setStatus("Kunde inte sätta roll: " + (await r.text()), "error");
        e.target.value = e.target.dataset.current;
      }
    });
  });
}

async function saveConfig() {
  saveBtn.disabled = true;
  setStatus("Sparar…");
  try {
    const r = await fetch("/admin/api/config", {
      method: "PUT",
      headers: { "Content-Type": "text/plain" },
      body: editor.value,
    });
    if (!r.ok) {
      const txt = await r.text();
      setStatus("Fel: " + txt, "error");
    } else {
      const j = await r.json();
      setStatus(`Sparad. ${j.measurements} mått, ${j.registration_keys} nycklar`, "success");
      refreshStatus();
    }
  } catch (e) {
    setStatus("Fel: " + e.message, "error");
  } finally {
    saveBtn.disabled = false;
  }
}

saveBtn.addEventListener("click", saveConfig);
reloadBtn.addEventListener("click", loadConfig);

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    const activeTab = document.querySelector(".tab.active").dataset.tab;
    if (activeTab === "form") saveFormConfig(); else saveConfig();
  }
});

// --- Tab-switch ---
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
  });
});

// --- Formulär: rendera + spara ---
const MEAS_TYPES = ["icmp_ping", "tcp_ping", "dns_query", "http_get", "traceroute"];
const TYPE_FIELDS = {
  icmp_ping: [{k: "packet_count", label: "Paket-count", type: "number"}, {k: "resolve_on_probe", label: "Resolve på probe", type: "checkbox"}],
  tcp_ping: [{k: "port", label: "Port", type: "number"}, {k: "timeout_ms", label: "Timeout (ms)", type: "number"}],
  dns_query: [{k: "query_name", label: "Query name", type: "text"}, {k: "query_type", label: "Query type", type: "text", default: "A"}],
  http_get: [{k: "expect_status", label: "Förväntad status", type: "number", default: 200}, {k: "timeout_seconds", label: "Timeout (s)", type: "number"}],
  traceroute: [{k: "max_hops", label: "Max hops", type: "number", default: 30}],
};

function setFormStatus(text, kind) {
  const el = document.getElementById("form-status");
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
}

async function loadFormConfig() {
  setFormStatus("Hämtar…");
  const r = await fetch("/admin/api/config.json");
  if (!r.ok) { setFormStatus("Fel: " + r.status, "error"); return; }
  const cfg = await r.json();

  document.getElementById("f-heartbeat").value = cfg.heartbeat_interval_seconds;
  document.getElementById("f-spec-validity").value = cfg.spec_validity_seconds;
  document.getElementById("f-peer-count").value = cfg.peer_count_per_probe;
  document.getElementById("f-peer-interval").value = cfg.peer_interval_seconds;

  renderStringList("f-reg-keys", cfg.registration_keys, "registreringsnyckel");
  renderStringList("f-nkn-ranges", cfg.nkn_public_ip_ranges, "CIDR (t.ex. 195.67.168.0/22)");
  renderCanaryList(cfg.canary_targets || []);
  renderMeasurementsList(cfg.builtin_measurements || []);

  setFormStatus("Laddad", "success");
}

function renderStringList(containerId, items, placeholder) {
  const el = document.getElementById(containerId);
  el.innerHTML = items.map((v, i) => `<div class="list-row">
    <input type="text" data-list="${containerId}" data-idx="${i}" value="${escapeHtml(v)}" placeholder="${placeholder}">
    <button class="secondary" data-rm="${containerId}" data-idx="${i}">x</button>
  </div>`).join("");
  el.querySelectorAll("[data-rm]").forEach(btn => {
    btn.addEventListener("click", () => {
      const list = btn.dataset.rm;
      const idx = parseInt(btn.dataset.idx, 10);
      const current = collectStringList(list);
      current.splice(idx, 1);
      renderStringList(list, current, placeholder);
    });
  });
}

function collectStringList(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} input[data-list]`))
    .map(inp => inp.value.trim()).filter(v => v);
}

function renderCanaryList(items) {
  const el = document.getElementById("f-canary");
  el.innerHTML = items.map((c, i) => `<div class="list-row">
    <input type="text" data-canary-target="${i}" value="${escapeHtml(c.target || '')}" placeholder="target (t.ex. ad-1.intern)">
    <input type="text" data-canary-desc="${i}" value="${escapeHtml(c.description || '')}" placeholder="beskrivning">
    <button class="secondary" data-rm-canary="${i}">x</button>
  </div>`).join("");
  el.querySelectorAll("[data-rm-canary]").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.rmCanary, 10);
      const current = collectCanaryList();
      current.splice(idx, 1);
      renderCanaryList(current);
    });
  });
}

function collectCanaryList() {
  const ids = new Set();
  document.querySelectorAll("#f-canary input[data-canary-target]").forEach(inp => ids.add(inp.dataset.canaryTarget));
  return Array.from(ids).map(i => ({
    target: document.querySelector(`#f-canary input[data-canary-target="${i}"]`).value.trim(),
    description: document.querySelector(`#f-canary input[data-canary-desc="${i}"]`).value.trim(),
  })).filter(c => c.target);
}

function renderMeasurementsList(items) {
  const el = document.getElementById("f-measurements");
  el.innerHTML = items.map((m, i) => renderMeasurementCard(m, i)).join("");
  attachMeasurementHandlers();
}

function renderMeasurementCard(m, idx) {
  const type = m.type || "icmp_ping";
  const knownKeys = new Set(["id", "type", "target", "interval_seconds", "category"]);
  const extra = Object.fromEntries(Object.entries(m).filter(([k]) => !knownKeys.has(k)));

  let typeFields = "";
  for (const f of (TYPE_FIELDS[type] || [])) {
    const val = extra[f.k] !== undefined ? extra[f.k] : (f.default !== undefined ? f.default : "");
    if (f.type === "checkbox") {
      typeFields += `<label>${f.label}<input type="checkbox" data-meas="${idx}" data-extra="${f.k}" ${val ? "checked" : ""}></label>`;
    } else {
      typeFields += `<label>${f.label}<input type="${f.type}" data-meas="${idx}" data-extra="${f.k}" value="${escapeHtml(String(val))}"></label>`;
    }
  }

  return `<div class="meas-card" data-meas-card="${idx}">
    <div class="meas-card-head">
      <span class="meas-type">${escapeHtml(type)}</span>
      <span class="meas-id">${escapeHtml(m.id || "(nytt mått)")}</span>
      <button class="secondary" data-rm-meas="${idx}">ta bort</button>
    </div>
    <div class="meas-fields">
      <label>id<input type="text" data-meas="${idx}" data-base="id" value="${escapeHtml(m.id || '')}"></label>
      <label>typ<select data-meas="${idx}" data-base="type">
        ${MEAS_TYPES.map(t => `<option value="${t}"${t === type ? ' selected' : ''}>${t}</option>`).join("")}
      </select></label>
      <label>target<input type="text" data-meas="${idx}" data-base="target" value="${escapeHtml(m.target || '')}"></label>
      <label>interval (s)<input type="number" data-meas="${idx}" data-base="interval_seconds" value="${m.interval_seconds || 60}"></label>
      ${typeFields}
    </div>
  </div>`;
}

function attachMeasurementHandlers() {
  document.querySelectorAll("[data-rm-meas]").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.rmMeas, 10);
      const current = collectMeasurements();
      current.splice(idx, 1);
      renderMeasurementsList(current);
    });
  });
  document.querySelectorAll('select[data-base="type"]').forEach(sel => {
    sel.addEventListener("change", () => {
      // Vid typ-byte måste vi rendera om kortet med rätt extra-fält
      const current = collectMeasurements();
      renderMeasurementsList(current);
    });
  });
}

function collectMeasurements() {
  const cards = document.querySelectorAll("[data-meas-card]");
  return Array.from(cards).map(card => {
    const idx = card.dataset.measCard;
    const m = {};
    card.querySelectorAll(`[data-meas="${idx}"][data-base]`).forEach(inp => {
      const key = inp.dataset.base;
      let val = inp.value.trim();
      if (key === "interval_seconds") val = parseInt(val, 10) || 60;
      m[key] = val;
    });
    card.querySelectorAll(`[data-meas="${idx}"][data-extra]`).forEach(inp => {
      const key = inp.dataset.extra;
      let val;
      if (inp.type === "checkbox") val = inp.checked;
      else if (inp.type === "number") val = parseInt(inp.value, 10);
      else val = inp.value.trim();
      if (val !== "" && val !== undefined && !Number.isNaN(val)) m[key] = val;
    });
    return m;
  }).filter(m => m.id && m.type && m.target);
}

document.querySelectorAll(".list-add").forEach(btn => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.add;
    if (target === "reg-keys") {
      const current = collectStringList("f-reg-keys");
      current.push("");
      renderStringList("f-reg-keys", current, "registreringsnyckel");
    } else if (target === "nkn-ranges") {
      const current = collectStringList("f-nkn-ranges");
      current.push("");
      renderStringList("f-nkn-ranges", current, "CIDR (t.ex. 195.67.168.0/22)");
    } else if (target === "canary") {
      const current = collectCanaryList();
      current.push({target: "", description: ""});
      renderCanaryList(current);
    } else if (target === "measurement") {
      const current = collectMeasurements();
      current.push({id: "nytt-matt-" + Date.now(), type: "icmp_ping", target: "", interval_seconds: 60});
      renderMeasurementsList(current);
    }
  });
});

async function saveFormConfig() {
  setFormStatus("Sparar…");
  const payload = {
    heartbeat_interval_seconds: parseInt(document.getElementById("f-heartbeat").value, 10),
    spec_validity_seconds: parseInt(document.getElementById("f-spec-validity").value, 10),
    peer_count_per_probe: parseInt(document.getElementById("f-peer-count").value, 10),
    peer_interval_seconds: parseInt(document.getElementById("f-peer-interval").value, 10),
    registration_keys: collectStringList("f-reg-keys"),
    nkn_public_ip_ranges: collectStringList("f-nkn-ranges"),
    canary_targets: collectCanaryList(),
    builtin_measurements: collectMeasurements(),
  };
  try {
    const r = await fetch("/admin/api/config.json", {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const txt = await r.text();
      setFormStatus("Fel: " + txt, "error");
    } else {
      const j = await r.json();
      setFormStatus(`Sparad. ${j.measurements} mått, ${j.registration_keys} nycklar`, "success");
      refreshStatus();
    }
  } catch (e) {
    setFormStatus("Fel: " + e.message, "error");
  }
}

document.getElementById("form-save").addEventListener("click", saveFormConfig);
document.getElementById("form-reload").addEventListener("click", loadFormConfig);

loadFormConfig();

async function refreshTraceroutes() {
  const r = await fetch("/admin/api/traceroute");
  if (!r.ok) return;
  const data = await r.json();
  const tbody = document.getElementById("traceroute-body");
  if (!data.items.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">Inga traceroute-mätningar ännu</td></tr>';
    return;
  }
  tbody.innerHTML = data.items.map(it => {
    const age = formatAge(it.timestamp);
    const total = it.total_ms !== null ? `${Math.round(it.total_ms)} ms` : "-";
    return `<tr class="tr-row" data-client="${escapeHtml(it.client_id)}" data-mid="${escapeHtml(it.measurement_id)}">
      <td>${escapeHtml(it.site_name) || '<span class="muted">-</span>'}</td>
      <td class="muted">${escapeHtml(it.measurement_id)}</td>
      <td>${escapeHtml(it.target) || '<span class="muted">-</span>'}</td>
      <td>${it.hops ?? '<span class="muted">-</span>'}</td>
      <td>${total}</td>
      <td class="${age.cls}">${age.text}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll(".tr-row").forEach(row => {
    row.addEventListener("click", () => toggleTracerouteDetail(row));
  });
}

async function toggleTracerouteDetail(row) {
  const next = row.nextElementSibling;
  if (next && next.classList.contains("tr-detail-row")) {
    next.remove();
    return;
  }
  const clientId = row.dataset.client;
  const mid = row.dataset.mid;
  const r = await fetch(`/admin/api/traceroute/${clientId}/${mid}?limit=20`);
  if (!r.ok) return;
  const data = await r.json();
  const detail = document.createElement("tr");
  detail.classList.add("tr-detail-row");
  const td = document.createElement("td");
  td.colSpan = 6;
  td.innerHTML = renderTracerouteDetail(data.paths);
  detail.appendChild(td);
  row.parentNode.insertBefore(detail, row.nextSibling);
}

function renderTracerouteDetail(paths) {
  if (!paths || !paths.length) return '<div class="tr-detail muted">Ingen historik</div>';
  const latest = paths[0];
  const path = latest.path || [];
  const hosts = latest.path_hosts || [];
  const hops = path.map((ip, i) => {
    const host = hosts[i];
    const hostHtml = host
      ? `<span class="tr-hop-ip">${escapeHtml(host)}</span> <span class="muted">(${escapeHtml(ip)})</span>`
      : `<span class="tr-hop-ip">${escapeHtml(ip)}</span>`;
    return `<div class="tr-hop"><span class="tr-hop-num">${i+1}.</span>${hostHtml}</div>`;
  }).join("");
  let history = "";
  if (paths.length > 1) {
    const lines = paths.slice(0, 20).map(p => {
      const age = formatAge(p.timestamp);
      const ttl = p.total_ms !== null ? `${Math.round(p.total_ms)}ms` : "-";
      return `${age.text} - ${p.hops || '?'} hops, ${ttl}`;
    }).join(" • ");
    history = `<div class="tr-history-line">Historik: ${lines}</div>`;
  }
  return `<div class="tr-detail">${hops}${history}</div>`;
}

async function sweepDeadProbes() {
  const hours = parseInt(document.getElementById("sweep-hours").value, 10) || 24;
  if (!confirm(`Ta bort probes som inte heartbeatat på ${hours} h?`)) return;
  const r = await fetch(`/admin/api/probes/sweep?older_than_hours=${hours}`, { method: "POST" });
  if (r.ok) {
    const j = await r.json();
    setStatus(`Rensade ${j.deleted} probes (>${j.older_than_hours} h)`, "success");
    refreshProbes();
    refreshStatus();
  } else {
    setStatus("Sweep-fel: " + (await r.text()), "error");
  }
}
document.getElementById("sweep-btn").addEventListener("click", sweepDeadProbes);

loadConfig();
refreshProbes();
refreshTraceroutes();
setInterval(refreshStatus, 10000);
setInterval(refreshProbes, 5000);
setInterval(refreshTraceroutes, 30000);
</script>
</body>
</html>
"""
