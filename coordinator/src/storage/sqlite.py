"""SQLite-lager för probe-registrering och token-validering.

Iteration 2: bara probes-tabellen. Sites och tags läggs till i Iteration 3+.
Tokens lagras som SHA-256-hash; klartext finns bara hos klienten.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS probes (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    hostname TEXT,
    site_name TEXT,
    ecclesiastical_unit TEXT,
    site_type TEXT,
    notes TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_heartbeat_at TEXT,
    last_seen_public_ip TEXT,
    last_classification TEXT,
    version TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_probes_token_hash ON probes(token_hash);

CREATE TABLE IF NOT EXISTS traceroute_paths (
    client_id TEXT NOT NULL,
    measurement_id TEXT NOT NULL,
    target TEXT,
    timestamp TEXT NOT NULL,
    path_json TEXT NOT NULL,
    hops INTEGER,
    total_ms REAL,
    PRIMARY KEY (client_id, measurement_id, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_traceroute_lookup
    ON traceroute_paths(client_id, measurement_id, timestamp DESC);
"""

_TRACEROUTE_RETENTION = 50  # behåll senaste N per (client, measurement)

# Kolumner som kan saknas i äldre databaser (Iteration 2 leverans 1+).
_MIGRATIONS: list[tuple[str, str]] = [
    ("last_seen_public_ip", "TEXT"),
    ("last_classification", "TEXT"),
    ("version", "TEXT"),
    ("last_local_ipv4_json", "TEXT"),
    ("role", "TEXT NOT NULL DEFAULT 'probe'"),
]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """Opakt 64-teckens hex-token. Endast för bearer-auth, inte ett lösenord."""
    return secrets.token_hex(32)


@dataclass
class Probe:
    id: str
    hostname: str | None
    site_name: str | None
    ecclesiastical_unit: str | None
    site_type: str | None
    enabled: bool


class Storage:
    """Tunn wrapper kring sqlite3 med trådsäker connection per anrop."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(probes)")}
            for col, ddl in _MIGRATIONS:
                if col not in existing:
                    conn.execute(f"ALTER TABLE probes ADD COLUMN {col} {ddl}")
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def register_probe(
        self,
        probe_id: str,
        token: str,
        hostname: str | None,
        site_name: str | None,
        ecclesiastical_unit: str | None,
        site_type: str | None,
        notes: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO probes (id, token_hash, hostname, site_name, "
                "ecclesiastical_unit, site_type, notes, enabled, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    probe_id,
                    hash_token(token),
                    hostname,
                    site_name,
                    ecclesiastical_unit,
                    site_type,
                    notes,
                    now,
                ),
            )
            conn.commit()

    def find_probe_by_token(self, token: str) -> Probe | None:
        h = hash_token(token)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, hostname, site_name, ecclesiastical_unit, site_type, enabled "
                "FROM probes WHERE token_hash = ?",
                (h,),
            ).fetchone()
        if row is None:
            return None
        return Probe(
            id=row["id"],
            hostname=row["hostname"],
            site_name=row["site_name"],
            ecclesiastical_unit=row["ecclesiastical_unit"],
            site_type=row["site_type"],
            enabled=bool(row["enabled"]),
        )

    def touch_heartbeat(self, probe_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE probes SET last_heartbeat_at = ? WHERE id = ?", (now, probe_id))
            conn.commit()

    def update_heartbeat_meta(
        self,
        probe_id: str,
        public_ip: str | None,
        classification: str | None,
        version: str | None,
        local_ipv4: list[str] | None = None,
    ) -> None:
        import json
        now = datetime.now(timezone.utc).isoformat()
        local_json = json.dumps(local_ipv4) if local_ipv4 else None
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE probes SET last_heartbeat_at = ?, last_seen_public_ip = ?, "
                "last_classification = ?, version = COALESCE(?, version), "
                "last_local_ipv4_json = COALESCE(?, last_local_ipv4_json) WHERE id = ?",
                (now, public_ip, classification, version, local_json, probe_id),
            )
            conn.commit()

    def count_probes(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM probes WHERE enabled = 1").fetchone()[0]

    def list_probes(self) -> list[dict]:
        import json
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, hostname, site_name, ecclesiastical_unit, site_type, "
                "enabled, last_heartbeat_at, last_seen_public_ip, last_classification, "
                "version, last_local_ipv4_json, role, created_at FROM probes ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            raw = d.pop("last_local_ipv4_json", None)
            try:
                d["last_local_ipv4"] = json.loads(raw) if raw else []
            except (ValueError, TypeError):
                d["last_local_ipv4"] = []
            result.append(d)
        return result

    def set_probe_role(self, probe_id: str, role: str) -> bool:
        if role not in ("probe", "anchor"):
            raise ValueError(f"Ogiltig role: {role}")
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE probes SET role = ? WHERE id = ?", (role, probe_id))
            conn.commit()
            return cur.rowcount > 0

    def save_traceroute_path(
        self,
        client_id: str,
        measurement_id: str,
        target: str,
        timestamp: str,
        path: list[str],
        hops: int | None,
        total_ms: float | None,
    ) -> None:
        import json
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO traceroute_paths "
                "(client_id, measurement_id, target, timestamp, path_json, hops, total_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (client_id, measurement_id, target, timestamp, json.dumps(path), hops, total_ms),
            )
            conn.execute(
                "DELETE FROM traceroute_paths "
                "WHERE client_id = ? AND measurement_id = ? "
                "AND timestamp NOT IN ("
                "  SELECT timestamp FROM traceroute_paths "
                "  WHERE client_id = ? AND measurement_id = ? "
                "  ORDER BY timestamp DESC LIMIT ?"
                ")",
                (client_id, measurement_id, client_id, measurement_id, _TRACEROUTE_RETENTION),
            )
            conn.commit()

    def get_traceroute_paths(
        self, client_id: str, measurement_id: str, limit: int = 50
    ) -> list[dict]:
        import json
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT timestamp, target, path_json, hops, total_ms "
                "FROM traceroute_paths "
                "WHERE client_id = ? AND measurement_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (client_id, measurement_id, limit),
            ).fetchall()
        out = []
        for r in rows:
            try:
                path = json.loads(r["path_json"])
            except (ValueError, TypeError):
                path = []
            out.append({
                "timestamp": r["timestamp"],
                "target": r["target"],
                "path": path,
                "hops": r["hops"],
                "total_ms": r["total_ms"],
            })
        return out

    def list_traceroute_pairs(self) -> list[dict]:
        """Returnera alla (client, measurement) med senaste timestamp och hops-count."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.client_id, t.measurement_id, t.target, t.timestamp, t.hops, t.total_ms, "
                "       p.site_name, p.hostname "
                "FROM traceroute_paths t "
                "LEFT JOIN probes p ON p.id = t.client_id "
                "WHERE t.timestamp = ("
                "  SELECT MAX(timestamp) FROM traceroute_paths "
                "  WHERE client_id = t.client_id AND measurement_id = t.measurement_id"
                ") "
                "ORDER BY p.site_name, t.measurement_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_dead_probes(self, older_than_hours: int) -> int:
        """Ta bort probes som inte heartbeatat på X timmar.

        Probes som aldrig fått en heartbeat tas bort om de skapades för
        mer än X timmar sedan (typiskt re-registrerade och övergivna).
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        with self._lock, self._connect() as conn:
            cur1 = conn.execute(
                "DELETE FROM probes WHERE last_heartbeat_at IS NOT NULL "
                "AND last_heartbeat_at < ?",
                (cutoff,),
            )
            cur2 = conn.execute(
                "DELETE FROM probes WHERE last_heartbeat_at IS NULL AND created_at < ?",
                (cutoff,),
            )
            conn.commit()
            return cur1.rowcount + cur2.rowcount


def open_storage() -> Storage:
    return Storage(os.getenv("DATABASE_PATH", "/app/data/coordinator.db"))
