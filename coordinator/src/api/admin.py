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

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..config import CoordinatorConfig

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
  .age-fresh { color: var(--success); }
  .age-stale { color: var(--warn); }
  .age-dead { color: var(--error); }
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
      <h2>config.yaml</h2>
      <textarea id="editor" spellcheck="false" autocomplete="off"></textarea>
      <div class="toolbar">
        <button id="save">Spara &amp; reload</button>
        <button class="secondary" id="reload">Hämta från disk</button>
        <span id="status" class="status">Laddar…</span>
      </div>
      <p class="hint"><kbd>Ctrl</kbd>+<kbd>S</kbd> sparar. Filen valideras innan den skrivs.</p>
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
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:12px;">
      <h2 style="margin:0;">Registrerade probes</h2>
      <span style="flex:1;"></span>
      <label class="hint" style="margin:0;">
        Rensa probes äldre än
        <input id="sweep-hours" type="number" min="1" value="24" style="width:60px; padding:2px 6px; background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:3px;"> h
      </label>
      <button class="secondary" id="sweep-btn">Rensa döda</button>
    </div>
    <table class="probes">
      <thead>
        <tr>
          <th>Site</th>
          <th>Hostname</th>
          <th>Typ</th>
          <th>Klassificering</th>
          <th>Publik IP</th>
          <th>Senaste heartbeat</th>
          <th>Version</th>
        </tr>
      </thead>
      <tbody id="probes-body"></tbody>
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
  const seen = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - seen) / 1000);
  let text;
  if (seconds < 60) text = seconds + "s sen";
  else if (seconds < 3600) text = Math.round(seconds/60) + " min sen";
  else if (seconds < 86400) text = Math.round(seconds/3600) + " h sen";
  else text = Math.round(seconds/86400) + " d sen";
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
    return `<tr>
      <td>${escapeHtml(p.site_name) || '<span class="muted">-</span>'}</td>
      <td>${escapeHtml(p.hostname) || '<span class="muted">-</span>'}</td>
      <td class="muted">${escapeHtml(p.site_type) || '-'}</td>
      <td><span class="badge ${cls}">${escapeHtml(cls)}</span></td>
      <td>${escapeHtml(p.last_seen_public_ip) || '<span class="muted">-</span>'}</td>
      <td class="${age.cls}">${age.text}</td>
      <td class="muted">${escapeHtml(p.version) || '-'}</td>
    </tr>`;
  }).join("");
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
    saveConfig();
  }
});

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
setInterval(refreshStatus, 10000);
setInterval(refreshProbes, 5000);
</script>
</body>
</html>
"""
