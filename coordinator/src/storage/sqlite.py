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
"""

# Kolumner som kan saknas i äldre databaser (Iteration 2 leverans 1).
_MIGRATIONS: list[tuple[str, str]] = [
    ("last_seen_public_ip", "TEXT"),
    ("last_classification", "TEXT"),
    ("version", "TEXT"),
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
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE probes SET last_heartbeat_at = ?, last_seen_public_ip = ?, "
                "last_classification = ?, version = COALESCE(?, version) WHERE id = ?",
                (now, public_ip, classification, version, probe_id),
            )
            conn.commit()

    def count_probes(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM probes WHERE enabled = 1").fetchone()[0]

    def list_probes(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, hostname, site_name, ecclesiastical_unit, site_type, "
                "enabled, last_heartbeat_at, last_seen_public_ip, last_classification, "
                "version, created_at FROM probes ORDER BY created_at DESC"
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
