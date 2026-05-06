# NKN-Monitor

Crowdsourcat övervakning av Nya Kyrknätet (NKN) – inspirerat av RIPE Atlas.
Lokala Windows-klienter rapporterar in nätmätningar (RTT, paketförlust, DNS, HTTP)
till en central FastAPI-koordinator. Tidsserier lagras i VictoriaMetrics och
visualiseras i Grafana.

Se [`SPECIFICATION.md`](./SPECIFICATION.md) för fullständig design.

## Status

**Iteration 1 (PoC):** end-to-end-skelett.

- [x] Coordinator (`/healthz`, `/probe/register`, `/probe/results`)
- [x] VictoriaMetrics + Grafana via docker-compose
- [x] Mock-klient i Python som genererar testdata
- [x] Grafana-dashboard som visar `nkn_ping_rtt_ms` över tid
- [ ] Riktig PowerShell-klient (Iteration 2+)
- [ ] Spec-distribution, peer-mätning, NKN-klassificering

## Snabbstart (utvecklingsmiljö)

Hela serversidan körs som en docker-compose-stack på en Linux-host (i nuläget
`ubuntu-ai`-VMen i hemnätet, nåbar via Tailscale).

```bash
# Bara serversidan
docker compose up -d --build

# Med 5 simulerade probes som genererar testdata
docker compose --profile mock up -d --build
```

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
| Coordinator     | **8200**  | 8000           | port 8000 var upptagen på hosten |
| VictoriaMetrics | 8428      | 8428           | exponeras endast i dev           |
| Grafana         | 3000      | 3000           |                                  |

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
  -d '{"registration_key":"dev","client_metadata":{"hostname":"smoke-1","site_name":"Smoketest"}}' \
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

## Produktion

`docker-compose.prod.yml` lägger till Caddy som reverse proxy och tar bort
hot-reload + utåtexponerade interna portar. `.env.example` listar de
miljövariabler som behöver sättas. Hosting-target är inte fastställt.
