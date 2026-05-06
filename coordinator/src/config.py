"""Inläsning av coordinator-config från YAML."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CoordinatorConfig:
    heartbeat_interval_seconds: int = 300
    spec_validity_seconds: int = 3600
    registration_keys: list[str] = field(default_factory=list)
    builtin_measurements: list[dict[str, Any]] = field(default_factory=list)
    canary_targets: list[dict[str, Any]] = field(default_factory=list)
    nkn_public_ip_ranges: list[str] = field(default_factory=list)
    peer_count_per_probe: int = 3
    peer_interval_seconds: int = 300

    @classmethod
    def from_file(cls, path: Path) -> "CoordinatorConfig":
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls(
            heartbeat_interval_seconds=int(raw.get("heartbeat_interval_seconds", 300)),
            spec_validity_seconds=int(raw.get("spec_validity_seconds", 3600)),
            registration_keys=list(raw.get("registration_keys", [])),
            builtin_measurements=list(raw.get("builtin_measurements", [])),
            canary_targets=list(raw.get("canary_targets", [])),
            nkn_public_ip_ranges=list(raw.get("nkn_public_ip_ranges", [])),
            peer_count_per_probe=int(raw.get("peer_count_per_probe", 3)),
            peer_interval_seconds=int(raw.get("peer_interval_seconds", 300)),
        )


def load_config() -> CoordinatorConfig:
    path = Path(os.getenv("COORDINATOR_CONFIG", "/app/config.yaml"))
    if not path.exists():
        # Fallback för importtester och dev utan mountad fil
        return CoordinatorConfig(registration_keys=["dev-registration-key"])
    return CoordinatorConfig.from_file(path)
