"""Distribuera senaste PowerShell-klient till probes via heartbeat.

Vid coordinator-start läses `client/NknMonitor.ps1` (mountad in på
/app/client/NknMonitor.ps1) och dess version + SHA-256 cachas. Heartbeat-
handlern jämför mot probens egen version och inkluderar update-erbjudande
i svaret.

Version utvinns ur header-kommentaren `# Version: X.Y.Z`. Skript som
saknar header-versionen distribueras inte (säkerhetsåtgärd: vi vill inte
servera filer som inte är tydligt märkta).
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

_VERSION_RE = re.compile(rb"#\s*Version:\s*([\d]+\.[\d]+\.[\d]+)")


def _client_path() -> Path:
    return Path(os.getenv("CLIENT_SCRIPT_PATH", "/app/client/NknMonitor.ps1"))


@dataclass
class ClientInfo:
    version: str | None
    sha256: str | None
    size: int
    path: Path

    @classmethod
    def load(cls) -> "ClientInfo":
        path = _client_path()
        if not path.exists():
            return cls(version=None, sha256=None, size=0, path=path)
        data = path.read_bytes()
        m = _VERSION_RE.search(data)
        version = m.group(1).decode("ascii") if m else None
        return cls(
            version=version,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            path=path,
        )

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()
