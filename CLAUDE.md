# CLAUDE.md – NKN-Monitor

Kontext för Claude Code i detta repo. För fullständig design, se
[`SPECIFICATION.md`](./SPECIFICATION.md).

## Projektet i en mening

Crowdsourcad nätövervakning av Nya Kyrknätet: Windows-klienter pingar mot
gemensamma och lokala mål, rapporterar till en FastAPI-koordinator, data hamnar
i VictoriaMetrics och visas i Grafana. Inspirerat av RIPE Atlas.

## Status

**Iteration 1 (PoC).** End-to-end-flöde mock-klient → coordinator →
VictoriaMetrics → Grafana fungerar. Resterande iterationer (riktig
PowerShell-klient, spec-distribution, peer-mätning, NKN-klassificering, DB)
byggs ovanpå detta skelett. Se §13 i `SPECIFICATION.md` för vägkartan.

## Stack

- **Coordinator:** Python 3.12 + FastAPI + httpx, `pyproject.toml`,
  `uv`-baserad install. Multi-stage Dockerfile (`dev` / `production`).
- **Tidsseriedata:** VictoriaMetrics (single-node). Skrivs via Influx line
  protocol mot `/write`. Läses som Prometheus-datakälla i Grafana.
- **Metadata-DB:** PostgreSQL **planerat** för Iteration 2+, ej igång ännu.
- **Visualisering:** Grafana med provisionerade datakällor och dashboards.
- **Mock-klient:** Python + httpx, simulerar N probes (`normal`, `degraded`,
  `offline-bursts`).
- **Riktig klient:** PowerShell, utvecklas separat på Windows-host (ej i repo).
- **Reverse proxy / TLS:** Caddy i `docker-compose.prod.yml`.

## Filstruktur

```
nkn-mon/
├── README.md
├── SPECIFICATION.md
├── docker-compose.yml          # dev-stack + mock-profile
├── docker-compose.prod.yml     # prod-overrides (Caddy, restart-policies, env)
├── .env.example
├── coordinator/
│   ├── Dockerfile              # multi-stage: dev + production
│   ├── pyproject.toml
│   └── src/
│       ├── main.py             # FastAPI-app, endpoints, modeller
│       └── vm.py               # VictoriaMetrics-skrivning (Influx line protocol)
├── mock-client/
│   ├── Dockerfile
│   └── mock_probe.py
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/victoriametrics.yml
│   │   └── dashboards/default.yml
│   └── dashboards/overview.json
└── caddy/                      # placeholder för prod-Caddyfile
```

## Designbeslut värda att minnas

- **Port 8200 på hosten** mappar coordinatorns container-port 8000. Anledningen
  är att 8000 var upptagen på `ubuntu-ai`. Klienter anropar
  `http://ubuntu-ai:8200` över Tailscale i dev.
- **Ingen DB i Iteration 1.** `/probe/register` returnerar en hårdkodad
  MVP-token (env `MVP_TOKEN`) och ett UUID. Läggs på riktigt i Iteration 2.
- **Ingen passiv data, ingen lyssnande klient.** Klienten initierar all trafik
  utgående. Detta är en grundprincip från Atlas-modellen.
- **Sanering före Grafana.** Tag-värden eskapas i `vm.py` innan de skrivs till
  VictoriaMetrics – `=`, `,`, mellanslag i tag-värden får `\`-prefix. Ny
  fält-validering måste passera samma rutin.

## Vanliga ändringar

- **Lägg till mättyp:** Lägg till modell i `coordinator/src/main.py`, generera
  rader i `coordinator/src/vm.py` (`build_lines`), uppdatera mock-klienten,
  lägg ev. till panel i `grafana/dashboards/overview.json`.
- **Bryta ut DB-lager:** Skapa `coordinator/src/storage/postgres.py`, läs
  `DATABASE_URL`, init-tabeller via `init_db()` (matchar §6 i spec).
- **Ny endpoint:** Lägg under `coordinator/src/api/<domän>.py` när routes växer
  förbi en fil. Importera delade dependencies från en framtida
  `coordinator/src/deps.py` (finns inte ännu, skapa när behovet uppstår).

## Miljövariabler

| Variabel              | Default (dev)                | Anmärkning                          |
|-----------------------|------------------------------|-------------------------------------|
| `VICTORIAMETRICS_URL` | `http://victoriametrics:8428`| Coordinator → VM                    |
| `MVP_TOKEN`           | `mvp-fixed-token-dev`        | Hårdkodad probe-token i Iteration 1 |
| `LOG_LEVEL`           | `INFO`                       |                                     |
| `COORDINATOR_URL`     | `http://coordinator:8000`    | Mock-klient → coordinator           |
| `MOCK_PROBES`         | `5`                          | Antal simulerade probes             |
| `MOCK_INTERVAL_SECONDS` | `30`                       | Sekunder mellan rapporter           |
| `MOCK_SCENARIO`       | `normal`                     | `normal` / `degraded` / `offline-bursts` |

## Verifiering

Stacken kan inte startas av Claude (saknar dockergrupps-access). Verifiering
sker av användaren med:

```bash
docker compose --profile mock up -d --build
curl http://localhost:8200/healthz
# Öppna http://<host>:3000 → dashboard "NKN-Monitor – översikt"
```

Snabb importtest av coordinator-koden:

```bash
cd coordinator
uv run --with fastapi --with httpx --with pydantic python -c "from src.main import app; print('OK')"
```

## Vad jag inte ska göra

- Implementera funktioner från Iteration 2+ "på köpet" – håll scope till
  Iteration 1 tills användaren ber om mer.
- Skicka klient-IP, hostname eller andra identifierande fält till Grafana
  utan att uttryckligen diskutera GDPR-konsekvenserna (§3.4).
- Lyssna på inkommande portar i klienten – brott mot grundprincipen.
- Använda 8000 på hosten – kollisionerar.
