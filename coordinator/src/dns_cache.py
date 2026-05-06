"""Async reverse-DNS-cache för traceroute-hop-IPs.

Slår upp PTR-records via asyncio och cachar resultaten i minne med TTL.
Misslyckade uppslag (NXDOMAIN, timeout) cachas också så vi inte gör om dem
på nytt vid varje render. Hostname-värden där PTR returnerar IP:n själv
filtreras bort - de är inte meningsfulla.
"""
from __future__ import annotations

import asyncio
import socket
import time


class HostnameCache:
    def __init__(self, ttl_seconds: int = 3600, fail_ttl_seconds: int = 300):
        self._cache: dict[str, tuple[str | None, float]] = {}
        self._ttl = ttl_seconds
        self._fail_ttl = fail_ttl_seconds
        self._inflight: dict[str, asyncio.Task] = {}

    async def _lookup_one(self, ip: str) -> str | None:
        try:
            loop = asyncio.get_event_loop()
            host, _ = await asyncio.wait_for(
                loop.getnameinfo((ip, 0), 0), timeout=0.5
            )
        except (asyncio.TimeoutError, socket.gaierror, OSError):
            return None
        if host == ip or not host:
            return None
        return host

    async def get(self, ip: str) -> str | None:
        now = time.time()
        cached = self._cache.get(ip)
        if cached is not None:
            host, at = cached
            ttl = self._ttl if host else self._fail_ttl
            if now - at < ttl:
                return host

        # Coalesce parallella anrop för samma IP
        if ip in self._inflight:
            return await self._inflight[ip]

        task = asyncio.create_task(self._lookup_one(ip))
        self._inflight[ip] = task
        try:
            host = await task
            self._cache[ip] = (host, time.time())
            return host
        finally:
            self._inflight.pop(ip, None)

    async def get_many(self, ips: list[str]) -> dict[str, str | None]:
        results = await asyncio.gather(*(self.get(ip) for ip in ips))
        return dict(zip(ips, results))
