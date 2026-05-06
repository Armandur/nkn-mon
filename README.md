# NKN-Monitor

Crowdsourcat övervakning av Nya Kyrknätet (NKN) – inspirerat av RIPE Atlas.
Lokala Windows-klienter rapporterar in nätmätningar (RTT, paketförlust, DNS, HTTP)
till en central FastAPI-koordinator. Tidsserier lagras i VictoriaMetrics och
visualiseras i Grafana.

Se [`SPECIFICATION.md`](./SPECIFICATION.md) för fullständig design.

## Status

Iteration 1-3 klara. Iteration 4 till stor del klar (anchor + traceroute).
Iteration 5 påbörjad (single-image för Unraid, GitHub Actions, GDPR-doc).
Klienten är på v0.4 med fullt mätstöd.

| Område | Status |
|--------|--------|
| Coordinator (`/healthz`, `/probe/register`, `/probe/spec`, `/probe/heartbeat`, `/probe/results`) | ✅ |
| Centralt config-yaml + admin-UI med formulär OCH rå-YAML-tab | ✅ |
| Hot-reload av config (formulär eller YAML) | ✅ |
| SQLite: probes, traceroute-paths med rolling retention | ✅ |
| Bearer-tokens per probe (SHA-256, klartext bara hos klient) | ✅ |
| 401-handling i klient: re-registrerar automatiskt | ✅ |
| **Mättyper i klient:** icmp_ping, tcp_ping, dns_query, http_get, traceroute | ✅ |
| Per-mått-intervall (klienten respekterar `interval_seconds` i specen) | ✅ |
| Heartbeat + network context check (lokala IP, gateway, DNS, canaries, host-info) | ✅ |
| NKN-klassificering (publik IP mot konfigurerade ranges) | ✅ |
| Lokal JSONL-buffring i klienten vid offline (7 d retention) | ✅ |
| **Peer-mätning** med subnet-uteslutning + daglig rotation, anchors prioriteras | ✅ |
| **Reverse-DNS för traceroute-hops** i klientens nät | ✅ |
| Sju Grafana-dashboards: översikt, site-detalj, latency-matris, tystnande probes, SLA, peer-graf, traceroute-graf | ✅ |
| Admin-UI: probes-tabell, role-väljare (probe/anchor), sweep, traceroute-historik | ✅ |
| Single-image (`Dockerfile`) för Unraid med s6-overlay | ✅ |
| GitHub Actions bygger till `ghcr.io/armandur/nkn-mon` | ✅ |
| GDPR-dokumentation | ✅ |
| **Klient-uppdateringsmekanism via heartbeat** (auto-uppdatering med SHA-256-verifiering) | ✅ |
| **Per-probe delete + sweep i admin-UI** | ✅ |
| **DPAPI-skydd av token i klientens config.json** | ✅ |
| **Klock-sync-koll** mot serverns Date-header vid uppstart | ✅ |
| **`-LogFile`-parameter** för Scheduled Task-körning | ✅ |
| iperf3-stöd (Iteration 4) | ⏳ |
| User-defined mätningar per probe (Iteration 4) | ⏳ |
| OIDC-login mot SVK IDP (Iteration 5) | ⏳ |
| Authenticode-signering av klient (Iteration 5) | ⏳ |
| Scheduled Task-installer | ⏳ |

## Snabbstart (utvecklingsmiljö)

Hela serversidan körs som en docker-compose-stack på en Linux-host (i nuläget
`ubuntu-ai`-VMen i hemnätet, nåbar via Tailscale).

```bash
# Bara serversidan
docker compose up -d --build

# Med 15 simulerade probes som genererar testdata (default MOCK_PROBES=15)
docker compose --profile mock up -d --build
```

För deployment på Unraid eller annan host: använd single-image
(`Dockerfile`) – se [`docs/unraid-deployment.md`](./docs/unraid-deployment.md).

Verifiera:

```bash
curl http://localhost:8200/healthz                 # coordinator
curl http://localhost:8428/health                  # VictoriaMetrics
curl http://localhost:3000/api/health              # Grafana
```

Öppna sedan Grafana på <http://localhost:3000> (eller `http://ubuntu-ai:3000`
över Tailscale). Logga in som `admin/admin`. Dashboarden "NKN-Monitor – översikt"
ligger under mappen *NKN-Monitor*.

### Portar (host)

| Tjänst          | Host-port | Container-port | Anmärkning                       |
|-----------------|-----------|----------------|----------------------------------|
| Coordinator     | **8200**  | 8000           | API + admin-UI på `/admin/`      |
| VictoriaMetrics | 8428      | 8428           | exponeras endast i dev           |
| Grafana         | 3000      | 3000           |                                  |

### Admin-UI

http://ubuntu-ai:8200/admin/ – HTTP Basic auth (`ADMIN_USER` / `ADMIN_TOKEN`,
default `admin` / `admin-dev`). Innehåller:

- **Konfiguration** – formulär-UI eller rå YAML, hot-reload vid spara (Ctrl+S)
- **Status** – antal probes, mått, registreringsnycklar, senaste reload
- **Registrerade probes** – tabell med klassificering, lokal IP, heartbeat-ålder, role-dropdown (probe/anchor), sweep av döda
- **Senaste traceroute** – par av (probe, mål) med klickbar drop-down för hop-historik

> Admin-UI får aldrig exponeras publikt utan att `ADMIN_TOKEN` bytts ut.
> Default-token loggar varning vid uppstart.

Klienten (riktig eller mock på extern host) anropar coordinatorn på
`http://<tailscale-host>:8200`. För dev i hemnätet: `http://ubuntu-ai:8200`.

### Mock-klientens scenarier

`MOCK_SCENARIO`:

- `normal` (default) – RTT 5-50 ms, sällsynta loss-events
- `degraded` – RTT 40-200 ms, varierande paketförlust
- `offline-bursts` – ~15 % av rapporterna är `success=false`

Antal probes: `MOCK_PROBES` (default 5). Intervall: `MOCK_INTERVAL_SECONDS`
(default 30).

### Manuell smoke-test utan mock-klienten

```bash
# Registrera
TOKEN=$(curl -s -X POST http://localhost:8200/probe/register \
  -H "Content-Type: application/json" \
  -d '{"registration_key":"dev-registration-key","client_metadata":{"hostname":"smoke-1","site_name":"Smoketest"}}' \
  | jq -r .client_token)

# Skicka ett ping-resultat
curl -s -X POST http://localhost:8200/probe/results \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"client_id\": \"smoke-1\",
    \"results\": [{
      \"measurement_id\": \"builtin-ping-ad\",
      \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"type\": \"icmp_ping\",
      \"target\": \"ad-1.intern\",
      \"success\": true,
      \"rtt_ms_avg\": 12.3,
      \"packet_loss_pct\": 0,
      \"site\": \"Smoketest\"
    }]
  }"
```

## Repo-struktur

Se [`SPECIFICATION.md` §12.6](./SPECIFICATION.md). Iteration 1 implementerar
endast en delmängd – PowerShell-klient, peer-logik, NKN-klassificering m.m.
tillkommer i senare iterationer.

## Klient-uppdatering (auto)

När en bumpad version (`# Version: X.Y.Z` i header + `$Script:NknClientVersion`)
checks in och CI bygger ny single-image får alla probes erbjudandet vid
nästa heartbeat:

1. Coordinator parsar version + SHA-256 vid uppstart från
   `/app/client/NknMonitor.ps1`
2. Heartbeat-respons inkluderar `client_update` om probens version skiljer
3. Klientens `Update-Self` laddar ner via auth:ad request, verifierar
   SHA-256, ersätter filen på disk, exit 0
4. Original-skriptet sparas som `.old` för manuell rollback
5. Vid nästa körning (Scheduled Task eller manuell) kör nya versionen

Endpoints (kräver bearer-token):
- `GET /probe/client/version` – metadata om senaste klientversion
- `GET /probe/client/download` – binär nedladdning med
  `X-Client-Version` + `X-Client-SHA256`-headers

## Produktion

`docker-compose.prod.yml` lägger till Caddy som reverse proxy och tar bort
hot-reload + utåtexponerade interna portar. `.env.example` listar de
miljövariabler som behöver sättas. För Unraid-pilot: använd
nginx-proxy-manager och se `docs/unraid-deployment.md`.
