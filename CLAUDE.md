# CLAUDE.md – NKN-Monitor

Kontext för Claude Code i detta repo. För fullständig design, se
[`SPECIFICATION.md`](./SPECIFICATION.md).

## Projektet i en mening

Crowdsourcad nätövervakning av Nya Kyrknätet: Windows-klienter pingar mot
gemensamma och lokala mål, rapporterar till en FastAPI-koordinator, data hamnar
i VictoriaMetrics och visas i Grafana. Inspirerat av RIPE Atlas.

## Status

Iteration 1-3 klara, Iteration 4 till största delen klar
(anchor-stöd ✅, traceroute med reverse-DNS ✅; iperf3 ❌,
user-defined per probe ❌). Iteration 5 påbörjad: single-image
för Unraid, GitHub Actions till ghcr, GDPR-dokumentation. Klienten
är på v0.4 med samtliga mättyper, heartbeat, NKN-klassificering,
401-handling, lokal JSONL-buffring och per-mått-intervall.

Sju Grafana-dashboards levererade: översikt, site-detalj,
latency-matris, tystnande probes, SLA, peer-graf och traceroute-graf.

## Stack

- **Coordinator:** Python 3.12 + FastAPI + httpx + PyYAML, `pyproject.toml`,
  `uv`-baserad install. Multi-stage Dockerfile (`dev` / `production`) i
  `coordinator/Dockerfile` för dev-compose. Single-image i repo-rotens
  `Dockerfile` för Unraid-deployment (med s6-overlay).
- **Tidsseriedata:** VictoriaMetrics (single-node, flagga
  `--influxSkipSingleField`). Skrivs via Influx line protocol mot `/write`.
  Exponeras aldrig publikt - bara coordinator pratar med den.
- **Metadata-DB:** SQLite på coordinator (`/data/coordinator.db` i single-image).
  Tabeller: `probes`, `traceroute_paths` (rolling retention 50 per par).
- **Config:** `coordinator/config.yaml` (eller `/data/config.yaml` i single-image).
  Editeras via `/admin/`-UI med formulär ELLER rå YAML, hot-reload via
  PUT `/admin/api/config[.json]`.
- **Visualisering:** Grafana 11 med provisionerade datakällor (VictoriaMetrics
  + Coordinator-Graph via yesoreyeram-infinity-datasource för Node Graph-paneler).
  Sju dashboards i `grafana/dashboards/`.
- **Mock-klient:** Python + httpx, simulerar 15 probes som heartbeatar med
  fake publika IP:n inom NKN-range. Stöder icmp_ping + traceroute
  (med simulerade hostnames för hops).
- **Riktig klient:** PowerShell v0.4 (`client/NknMonitor.ps1`) på Windows-host.
  Alla mättyper, heartbeat, network context check, NKN-klassificering,
  per-mått-intervall, 401-handling, lokal JSONL-buffring, reverse-DNS
  för traceroute-hops i klientens nät.
- **Reverse proxy / TLS:** Caddy i `docker-compose.prod.yml` för Hetzner-prod.
  För Unraid-pilot: använd befintlig nginx-proxy-manager (se
  `docs/unraid-deployment.md`).
- **Image-distribution:** GitHub Actions bygger single-image till
  `ghcr.io/armandur/nkn-mon` vid push till main + tag-push.

## Filstruktur

```
nkn-mon/
├── README.md
├── SPECIFICATION.md
├── todo.md                     # roadmap + småfixar
├── Dockerfile                  # single-image för Unraid (s6-overlay)
├── docker-compose.yml          # dev-stack + mock-profile
├── docker-compose.prod.yml     # prod-overrides (Caddy, restart-policies, env)
├── .env.example
├── .github/workflows/
│   └── build-image.yml         # bygger single-image -> ghcr.io
├── docker/s6-overlay/          # cont-init + s6-rc.d-services för single-image
├── docs/
│   ├── gdpr.md                 # behandling av personuppgifter
│   └── unraid-deployment.md    # Unraid-template, NPM-konfig
├── coordinator/
│   ├── Dockerfile              # dev-compose multi-stage
│   ├── pyproject.toml
│   ├── config.yaml             # default config (kopieras till /data/ vid första start)
│   └── src/
│       ├── main.py             # FastAPI-app, /probe/*-endpoints, MeasurementResult
│       ├── config.py           # YAML-loader -> CoordinatorConfig
│       ├── vm.py               # VictoriaMetrics-skrivning + heartbeat-metrics
│       ├── classification.py   # publik IP -> nkn/external/unknown
│       ├── peers.py            # peer-tilldelning med subnet-uteslutning + rotation
│       ├── api/
│       │   └── admin.py        # /admin/-UI (formulär+YAML), /admin/api/*
│       └── storage/
│           └── sqlite.py       # probes + traceroute_paths
├── mock-client/
│   ├── Dockerfile
│   └── mock_probe.py           # heartbeat + simulerade traceroute-paths
├── client/
│   ├── NknMonitor.ps1          # v0.4: alla mättyper, heartbeat, buffer, traceroute+DNS
│   └── README.md
├── scripts/
│   └── sync-to-vmworkspace.sh  # speglar repo till /mnt/vmworkspace/nkn-mon
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   ├── victoriametrics.yml
│   │   │   └── coordinator-graph.yml   # Infinity-plugin för Node Graph
│   │   └── dashboards/default.yml
│   └── dashboards/             # sju dashboards (overview, site-detail, peer-graph, ...)
└── caddy/                      # placeholder för prod-Caddyfile
```

## Designbeslut värda att minnas

- **Port 8200 på hosten** mappar coordinatorns container-port 8000 i
  dev-compose. Anledningen är att 8000 var upptagen på `ubuntu-ai`.
  I single-image-deployment används användarens egna port-mappningar.
- **SQLite för coordinator-state.** Tabeller: `probes`, `traceroute_paths`.
  Bearer-tokens slumpgenereras per probe och lagras som SHA-256-hash;
  klartext finns bara hos klienten. Migration via `_MIGRATIONS`-lista i
  `Storage.__init__`.
- **VictoriaMetrics exponeras aldrig publikt.** `/write` saknar auth – om
  port 8428 nås från utsidan kan vem som helst skriva metric-data. I
  single-image lyssnar VM bara på 127.0.0.1, ingen port-exponering.
- **Admin-UI med formulär OCH rå YAML.** `/admin/` har två tabbar.
  Formulär-fliken serialiserar via `/admin/api/config.json` (parsar
  CoordinatorConfig), rå-YAML-fliken bevarar kommentarer via
  `/admin/api/config`. Bind-mount tillåter inte rename(2), så
  `tmp + replace` undveks medvetet.
- **NKN-klassificering på coordinator-sidan.** Klienten rapporterar
  publik IP, coordinator matchar mot `nkn_public_ip_ranges` i config.
  Klienten avgör inte själv om den är på NKN.
- **Klienten gör reverse-DNS.** Coordinator kan ligga externt (Hetzner)
  och saknar då tillgång till interna NKN-DNS. Klienten i sitt eget nät
  slår upp PTR-records och skickar `traceroute_path_hosts` parallellt
  med IP-arrayen.
- **Ingen passiv data, ingen lyssnande klient.** Klienten initierar all trafik
  utgående. Detta är en grundprincip från Atlas-modellen.
- **Sanering före Grafana.** Tag-värden eskapas i `vm.py` innan de skrivs till
  VictoriaMetrics – `=`, `,`, mellanslag i tag-värden får `\`-prefix. Ny
  fält-validering måste passera samma rutin.
- **Peer-mätning via subnet-uteslutning.** `assign_peers` filtrerar
  bort probes på samma /24 (= samma site), inkluderar alltid anchors,
  roterar resten med deterministisk hash av (probe_id + datum) så
  alla probes inte byter peers samma dag.

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
| `MOCK_PROBES`         | `15`                         | Antal simulerade probes             |
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
