# NKN-Monitor på Unraid

Single-image-deployment för Unraid där coordinator, VictoriaMetrics och
Grafana körs i en container under s6-overlay. Persistent state under
`/data`. Trafik exponeras via befintlig nginx-proxy-manager.

## Image

```
ghcr.io/armandur/nkn-mon:latest
```

Builds publiceras automatiskt vid push till `main` (se
`.github/workflows/build-image.yml`). Tag-format:

- `latest` – senaste main
- `main` – senaste main (samma som latest)
- `sha-<commit>` – specifik commit
- `v1.2.3` – semver vid tag-push

## Volym

En enda volym binds till `/data`:

```
/mnt/user/appdata/nkn-mon  →  /data
```

Innehåll efter första start:

```
/data/
├── config.yaml          # editerbar via admin-UI eller direkt
├── coordinator.db       # SQLite (probes, traceroute-paths)
├── grafana/             # Grafana-state, plugins, dashboards
└── vm/                  # VictoriaMetrics-data (90d retention default)
```

## Portar

| Port | Tjänst | Syfte |
|------|--------|-------|
| 8000 | Coordinator | API + admin-UI på `/admin/` |
| 3000 | Grafana | Dashboards |

VictoriaMetrics körs internt på 8428 men exponeras inte – det är ett
medvetet säkerhetsval (VM:s `/write` har ingen auth).

## Unraid Docker-mall (template)

| Fält | Värde |
|------|-------|
| Repository | `ghcr.io/armandur/nkn-mon:latest` |
| Network Type | `Bridge` |
| Path: Container Path = `/data`, Host Path = `/mnt/user/appdata/nkn-mon`, Access Mode = `Read/Write` |
| Port: Container = `8000`, Host = `8200` (eller annan ledig) |
| Port: Container = `3000`, Host = `3100` (eller annan ledig) |

## Miljövariabler

Alla har rimliga defaults men du bör ändra `ADMIN_TOKEN` innan publik
exponering.

| Variabel | Default | Anmärkning |
|----------|---------|------------|
| `ADMIN_TOKEN` | `admin-dev` | **Byt!** Skydd för admin-UI:t |
| `ADMIN_USER` | `admin` | |
| `GRAFANA_URL` | (tom) | Sätt till `https://nkn.example.se` så admin-UI:t får en länk till Grafana i headern |
| `LOG_LEVEL` | `INFO` | `DEBUG` för felsökning |
| `DATABASE_PATH` | `/data/coordinator.db` | Lämna |
| `COORDINATOR_CONFIG` | `/data/config.yaml` | Lämna |
| `VICTORIAMETRICS_URL` | `http://127.0.0.1:8428` | Lämna |

Grafana-admin har default `admin/admin`. Ändra vid första inloggning,
eller sätt `GF_SECURITY_ADMIN_PASSWORD` i container-env.

## nginx-proxy-manager

Två publika subdomäner föreslås:

| Subdomän | → | Anmärkning |
|----------|---|------------|
| `nkn-api.exempel.se` | `<unraid-ip>:8200` | Coordinator-API + admin-UI |
| `nkn.exempel.se` | `<unraid-ip>:3100` | Grafana |

Båda med autoTLS via NPM. För admin-UI:t (under `nkn-api.../admin/`)
rekommenderas IP-allowlist eller HTTP basic-auth via NPM utöver
admin-tokens, eftersom admin kan editera config för alla probes.

För Grafana: sätt `GF_SERVER_ROOT_URL=https://nkn.exempel.se` i
container-env så absoluta länkar och OAuth-callbacks (om aktivt) blir rätt.

## Första uppstart

1. Skapa volym-mappen `/mnt/user/appdata/nkn-mon` på Unraid
2. Sätt upp containern enligt template ovan
3. Starta containern
4. Vänta ~15 sek (s6-overlay startar VM, Grafana, coordinator i ordning)
5. Verifiera:
   - `curl http://<unraid-ip>:8200/healthz` → `{"status":"ok"}`
   - `http://<unraid-ip>:3100` → Grafana-login (admin/admin första gången)
6. Sätt upp NPM-routes
7. Logga in på `https://nkn-api.../admin/` (admin/admin-dev) och **byt admin-token**
8. Editera `/data/config.yaml` direkt eller via admin-UI:t

## Klient-anslutning

PowerShell-klienter pekas mot publika coordinator-URL:en:

```powershell
.\NknMonitor.ps1 -CoordinatorUrl https://nkn-api.exempel.se -RegistrationKey "..."
```

Registreringsnyckel ska finnas i `config.yaml` under `registration_keys`.

## Felsökning

Loggar:

```bash
docker logs <container-id> -f
```

Restart:

```bash
docker restart <container-id>
```

Reset av admin-lösen för Grafana (om du tappat det):

```bash
docker exec <container-id> /usr/share/grafana/bin/grafana cli \
    --homepath /usr/share/grafana admin reset-admin-password <nytt-pw>
```

`/data/config.yaml` kan editeras direkt på hosten – ändringar tas
upp vid omstart eller hot-reload via admin-UI:t.

## Klient-uppdatering

När en ny version av `client/NknMonitor.ps1` har commit:ats och CI byggt
en ny image får alla aktiva probes ett uppdateringserbjudande vid
nästa heartbeat:

1. På Unraid: pulla `:latest` och starta om container.
2. Coordinator läser nya skriptet (med `# Version: X.Y.Z` i headern)
   och beräknar SHA-256.
3. Klienter med annan version får `client_update` i sin nästa heartbeat.
4. Klienten `Update-Self` laddar ner skriptet (auth:at), verifierar
   SHA-256 och ersätter på disk. Original sparas som `.old` för rollback.
5. Klienten exit:ar efter ersättning. Med Scheduled Task som har
   "Restart on failure" startar den automatiskt.

Endpoints (kräver bearer-token):

- `GET /probe/client/version` – metadata
- `GET /probe/client/download` – binär PowerShell-fil

## Säkerhet

- Admin-UI exponerar config + probes-listor. Skydda bakom NPM med
  IP-allowlist eller stark token, helst båda.
- Ändra `admin/admin-dev` direkt vid uppstart.
- VictoriaMetrics nås bara internt i containern – ingen extra firewall
  behövs där.
- SQLite `coordinator.db` innehåller hashade probe-tokens (sha256), inga
  klartextlösenord. Säker att backa upp utan extra åtgärder.
