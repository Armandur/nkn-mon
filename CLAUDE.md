# CLAUDE.md – NKN-Monitor

Kontext för Claude Code i detta repo. För fullständig design, se
[`SPECIFICATION.md`](./SPECIFICATION.md).

## Projektet i en mening

Crowdsourcad nätövervakning av Nya Kyrknätet: Windows-klienter pingar mot
gemensamma och lokala mål, rapporterar till en FastAPI-koordinator, data hamnar
i VictoriaMetrics och visas i Grafana. Inspirerat av RIPE Atlas.

## Status

**Iteration 2 leverans 1.** Centralt config (`coordinator/config.yaml`),
admin-UI med hot-reload, SQLite för probe-registrering, första PowerShell-
klient med icmp_ping. Mock-klienten hämtar spec dynamiskt. Återstår i
leverans 2: övriga mättyper i klienten (tcp/dns/http), heartbeat + network
context check, NKN-klassificering, lokal buffring i klienten.

## Stack

- **Coordinator:** Python 3.12 + FastAPI + httpx + PyYAML, `pyproject.toml`,
  `uv`-baserad install. Multi-stage Dockerfile (`dev` / `production`).
- **Tidsseriedata:** VictoriaMetrics (single-node, flagga
  `--influxSkipSingleField`). Skrivs via Influx line protocol mot `/write`.
- **Metadata-DB:** SQLite på coordinator (`/app/data/coordinator.db` i container,
  named volume `coordinator-data`). Bara `probes`-tabellen i Iteration 2.
- **Config:** `coordinator/config.yaml` mountad in skrivbart. Editeras via
  `/admin/`-UI med hot-reload eller direkt på disk + omstart.
- **Visualisering:** Grafana med provisionerade datakällor och dashboards.
- **Mock-klient:** Python + httpx, hämtar spec, simulerar N probes
  (`normal`, `degraded`, `offline-bursts`).
- **Riktig klient:** PowerShell (`client/NknMonitor.ps1`), körs på Windows-host.
  v0.1 stöder bara icmp_ping; interaktiv registrering med default-värden från
  hostname.
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
│   ├── config.yaml             # centralt konfigställe (mountas in skrivbart)
│   └── src/
│       ├── main.py             # FastAPI-app, /probe/*-endpoints, modeller
│       ├── config.py           # YAML-loader -> CoordinatorConfig
│       ├── vm.py               # VictoriaMetrics-skrivning (Influx line protocol)
│       ├── api/
│       │   └── admin.py        # /admin/-UI och /admin/api/* (hot-reload)
│       └── storage/
│           └── sqlite.py       # probes-tabell, token-hash, registrering
├── mock-client/
│   ├── Dockerfile
│   └── mock_probe.py           # hämtar spec, simulerar icmp_ping
├── client/
│   ├── NknMonitor.ps1          # PowerShell-klient v0.1 (Windows-host)
│   └── README.md
├── scripts/
│   └── sync-to-vmworkspace.sh  # speglar repo till /mnt/vmworkspace/nkn-mon
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
- **SQLite från Iteration 2.** Probes registreras i en lokal SQLite-fil.
  Bearer-tokens slumpgenereras per probe och lagras som SHA-256-hash. Klartext
  finns bara hos klienten.
- **Admin-UI med hot-reload.** `/admin/` är HTTP Basic-skyddad (default
  `admin/admin-dev`, byt vid extern exponering). Spara i UI:t skriver YAML
  direkt till disk + uppdaterar `app.state.config`. Bind-mount tillåter inte
  rename(2), så `tmp + replace` undveks medvetet i `api/admin.py`.
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
| `COORDINATOR_CONFIG`  | `/app/config.yaml`           | YAML-config-fil i container         |
| `DATABASE_PATH`       | `/app/data/coordinator.db`   | SQLite för probes-tabellen          |
| `ADMIN_USER`          | `admin`                      | Basic auth för `/admin/`            |
| `ADMIN_TOKEN`         | `admin-dev`                  | Default loggar varning              |
| `LOG_LEVEL`           | `INFO`                       |                                     |
| `COORDINATOR_URL`     | `http://coordinator:8000`    | Mock-klient → coordinator           |
| `REGISTRATION_KEY`    | `dev-registration-key`       | Mock-klient & PowerShell-klient     |
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
