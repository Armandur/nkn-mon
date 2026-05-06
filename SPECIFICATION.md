# NKN-Monitor – Teknisk specifikation

**Projektnamn (arbetsnamn):** NKN-Monitor
**Status:** Planeringsfas / MVP-design
**Inspirerat av:** RIPE Atlas (https://atlas.ripe.net)

---

## 1. Bakgrund och syfte

Nya Kyrknätet (NKN) driftas av Global Connect och utgör Svenska kyrkans WAN. Centralt finns övervakning från Global Connects sida, men det saknas insyn i hur nätet upplevs *från de lokala noderna* – kyrkor, församlingsexpeditioner och satellitsiter. NKN-Monitor är ett crowdsourcat övervakningssystem där lokala datorer (typiskt stationära Windows 11-maskiner som alltid är på) frivilligt kör en lättviktsklient som utför aktiva nätverksmätningar och rapporterar in till en central koordinator. Resultatet visualiseras i Grafana.

**Mål:**

- Synliggöra lokal upplevelse av nätkvalitet (latency, paketförlust, DNS, bandbredd)
- Tidigt upptäcka problem på enskilda sites innan ärenden eskaleras
- Skapa underlag för dialog med Global Connect kring SLA och faktisk nätkvalitet
- Tillåta lokala administratörer att övervaka egna interna resurser (t.ex. satellitsiter)

**Icke-mål:**

- Detta är inte ett ersättning för centraliserad SNMP-övervakning eller IDS
- Klienten ska inte göra passiv trafikinspektion
- Klienten ska inte exponera några lokala tjänster (ingen webbserver, ingen lyssnande port)
- Inga personuppgifter samlas in från värddatorn

---

## 2. Övergripande arkitektur

```
┌─────────────────────────┐         ┌─────────────────────────┐
│  Klient (probe)         │         │  Klient (probe)         │
│  Windows 11             │         │  Windows 11             │
│  Site A (kyrka)         │         │  Site B (expedition)    │
└───────────┬─────────────┘         └───────────┬─────────────┘
            │ HTTPS (utgående)                   │
            │                                    │
            ▼                                    ▼
       ┌─────────────────────────────────────────────┐
       │  Koordinator (FastAPI)                      │
       │  - Registrering & autentisering             │
       │  - Mätuppdragsdistribution                  │
       │  - Resultatinsamling & validering           │
       │  - NKN-klassificering                       │
       └────────────┬──────────────────┬─────────────┘
                    │                  │
                    ▼                  ▼
         ┌──────────────────┐   ┌─────────────────────┐
         │  Tidsseriedata   │   │  Metadata-databas   │
         │  VictoriaMetrics │   │  PostgreSQL/SQLite  │
         └────────┬─────────┘   └─────────────────────┘
                  │
                  ▼
            ┌─────────────┐
            │  Grafana    │
            └─────────────┘
```

**Komponenter:**

1. **Klient (probe)** – Windows 11-applikation som kör mätningar i bakgrunden
2. **Anchors** – dedikerade mätnoder centralt placerade (Kyrkokansliet, ev. per stift), kör samma kod som probes men med förhöjt förtroende och fler uppgifter
3. **Koordinator** – central API-tjänst (FastAPI på Hetzner Cloud eller intern VM)
4. **Tidsseriedatabas** – VictoriaMetrics (rekommenderas för enkel drift och bra retention)
5. **Metadata-databas** – PostgreSQL eller SQLite för registrering, taggar, sites, peer-relationer
6. **Grafana** – visualisering, varningar

---

## 3. Designprinciper

### 3.1 Atlas-modellen som utgångspunkt

Vi följer RIPE Atlas övergripande designval där det är vettigt:

- **Probes initierar all kommunikation utåt.** Inga inkommande portar krävs på klientsidan. All command-and-control sker via långpollning eller WebSockets över HTTPS.
- **Klienten är "stum".** Klienten har ingen lokal konfiguration utöver registreringstoken. Allt – vilka mål, intervall, mättyp – kommer från koordinatorn. Detta gör att hela systemets beteende kan ändras centralt utan klientuppdatering.
- **Built-in vs user-defined measurements.** Varje klient kör en obligatorisk uppsättning baseline-mätningar (gemensam baslinje för all data) plus eventuellt user-defined mätningar (lokalt definierade interna mål för power-users).
- **Tags som primär organiseringsmekanism.** Klienter taggas med metadata (`stift:lulea`, `typ:expedition`, `wan:fiber`) och mätningar kan riktas mot tag-uttryck.

### 3.2 Avsteg från Atlas

- **Software-only probes.** Ingen dedikerad hårdvara. Klienten körs som Windows-tjänst eller schemalagd uppgift på befintliga datorer.
- **Inget credit-system.** Vi är en organisation med gemensamt mål, inte en global community.
- **Skala.** Sannolikt 50–500 probes, inte 12 000+. Tillåter enklare backend-val.

### 3.3 Säkerhet och förtroende

- Klienten lyssnar inte på något inkommande
- Klienten gör inga passiva mätningar
- All inrapportering valideras: en klient får bara rapportera resultat för mål som finns i dess tilldelade uppdragsspec
- Klient-tokens är revocable; varje klient autentiseras individuellt
- Metadata-fält saneras innan de når Grafana (ingen XSS via `enhet`-fältet)

### 3.4 GDPR-överväganden

- Inga användarnamn, e-postadresser eller AD-identiteter samlas in
- Klient-ID är ett opakt UUID, inte kopplat till användare
- Värddatorns hostname kan loggas men görs valfritt
- Publik IP samlas in för NKN-klassificering men exponeras inte i Grafana-vyer
- Retention på rådata bör begränsas (t.ex. 90 dagar full upplösning, sedan downsampling)

---

## 4. Klientspecifikation

### 4.1 Tekniskt val

**Rekommenderat:** PowerShell-script paketerat som schemalagd uppgift, med wrapper för enkel installation. Alternativt PS2EXE eller en .NET-binär (C#/F#) för minimal friktion vid utrullning.

**Kör som:**
- Windows Scheduled Task som triggas vid uppstart och kör i bakgrunden, eller
- Windows Service via NSSM/sc.exe

**Krav:**
- Windows 11 (primärt mål)
- Windows 10 (best effort)
- Inga admin-rättigheter under drift (endast vid installation)
- Minimal CPU- och bandbreddsanvändning (< 1% CPU genomsnitt, < 100 KB/s genomsnittlig bandbredd exkl. iperf)

### 4.2 Klientens livscykel

```
[Installation]
    │
    ▼
[Första uppstart] ──► [Registrering hos koordinator] ──► [Får token + initial spec]
    │
    ▼
[Huvudloop]
    │
    ├──► [Network context check] (var 5:e min)
    │       │
    │       └──► Är vi på NKN? Vilka kanariefåglar svarar? Publik IP?
    │
    ├──► [Heartbeat + context-rapport till koordinator] (var 5:e min)
    │       │
    │       └──► Får eventuellt uppdaterad spec
    │
    ├──► [Exekvera mätuppdrag enligt spec]
    │       │
    │       ├──► Ping/TCP-ping (var 1:a min)
    │       ├──► DNS-uppslagning (var 5:e min)
    │       ├──► HTTP-check (var 5:e min)
    │       └──► iperf (1 ggr per dygn, schemalagt nattetid)
    │
    └──► [Inrapportering till koordinator]
            │
            ├──► Om online: skicka direkt
            └──► Om offline: buffra lokalt (SQLite), försök igen senare
```

### 4.3 Network context check

Klienten avgör inte själv om den är på NKN. Den rapporterar fakta, koordinatorn klassificerar.

Vid varje heartbeat skickar klienten:

```json
{
  "client_id": "uuid",
  "timestamp": "ISO8601",
  "version": "0.1.0",
  "network_context": {
    "public_ip": "x.x.x.x",
    "local_ipv4": ["10.x.x.x"],
    "default_gateway": "10.x.x.1",
    "dns_servers": ["10.x.x.53"],
    "domain_membership": "svenskakyrkan.se",
    "canary_results": [
      {"target": "ad-1.intern", "reachable": true, "rtt_ms": 12.4},
      {"target": "smtp.intern", "reachable": true, "rtt_ms": 8.1}
    ]
  },
  "host_info": {
    "os": "Windows 11 23H2",
    "uptime_hours": 142.5
  }
}
```

Koordinatorn returnerar i sitt svar:
- `network_classification`: `"nkn" | "external" | "unknown"`
- Eventuellt uppdaterad mätspec baserat på klassificering

### 4.4 Mätuppdragsspec (från koordinator till klient)

```json
{
  "spec_version": "2026-05-06T10:00:00Z",
  "valid_until": "2026-05-06T11:00:00Z",
  "measurements": [
    {
      "id": "builtin-ping-ad",
      "category": "builtin",
      "type": "icmp_ping",
      "target": "ad-1.intern",
      "interval_seconds": 60,
      "packet_count": 4,
      "resolve_on_probe": true
    },
    {
      "id": "builtin-tcp-smtp",
      "category": "builtin",
      "type": "tcp_ping",
      "target": "smtp.intern",
      "port": 587,
      "interval_seconds": 60
    },
    {
      "id": "builtin-dns",
      "category": "builtin",
      "type": "dns_query",
      "target": "10.x.x.53",
      "query_name": "intra.svenskakyrkan.se",
      "query_type": "A",
      "interval_seconds": 300
    },
    {
      "id": "builtin-http-intranet",
      "category": "builtin",
      "type": "http_get",
      "target": "https://intra.svenskakyrkan.se/health",
      "interval_seconds": 300,
      "expect_status": 200
    },
    {
      "id": "peer-abc123",
      "category": "peer",
      "type": "icmp_ping",
      "target": "10.y.y.y",
      "peer_client_id": "abc123",
      "peer_site": "kyrka-falun",
      "interval_seconds": 300
    },
    {
      "id": "user-satellite-1",
      "category": "user_defined",
      "type": "icmp_ping",
      "target": "10.z.z.z",
      "label": "Satellitkyrka Norra",
      "interval_seconds": 300
    },
    {
      "id": "builtin-iperf",
      "category": "builtin",
      "type": "iperf3",
      "target": "anchor-stockholm.intern",
      "port": 5201,
      "schedule_cron": "0 3 * * *",
      "duration_seconds": 10,
      "direction": "downstream"
    }
  ]
}
```

### 4.5 Mättyper

| Typ | Verktyg/PowerShell | Output |
|---|---|---|
| `icmp_ping` | `Test-Connection` | RTT min/avg/max, paketförlust |
| `tcp_ping` | `Test-NetConnection -Port` eller egen socket-baserad | RTT, success/fail |
| `dns_query` | `Resolve-DnsName` | RTT, returkod, antal records |
| `http_get` | `Invoke-WebRequest`/`HttpClient` | Status, total tid, TTFB |
| `iperf3` | `iperf3.exe` (medlevereras) | Bandbredd up/down, retransmits |
| `traceroute` | `Test-NetConnection -TraceRoute` | Hop-lista (begränsad användning) |

**iperf3-noteringar:**
- Körs sällan (1 ggr/dygn) och bara mot dedikerade anchors, aldrig mot peer-klienter
- Begränsad duration (10 sek)
- Schemaläggs spritt över natten för att inte överbelasta anchors

### 4.6 Lokal buffring

När klienten inte kan nå koordinatorn (offline, NKN-strul):
- Buffra mätresultat i lokal SQLite (`%LOCALAPPDATA%\NKN-Monitor\buffer.db`)
- Behåll upp till 7 dagars data lokalt
- Försök inrapportera vid varje lyckad heartbeat
- Markera resultat med deras faktiska timestamp, inte inrapporteringstid

### 4.7 Klient-uppdatering

- Klienten kollar i heartbeat-svaret om en nyare version finns
- Vid större versionshopp: ladda ner från koordinator, byt ut sig själv, starta om
- Signering av binär bör övervägas i produktion (Authenticode)

---

## 5. Koordinator-API

Bas-URL: `https://nkn-monitor.svenskakyrkan.se/api/v1` (eller motsvarande)

All kommunikation över HTTPS. Bearer-token i Authorization-header efter registrering.

### 5.1 Endpoints

#### `POST /probe/register`

Anropas vid första uppstart. Kräver registreringsnyckel (förkonfigurerad eller engångskod).

**Request:**
```json
{
  "registration_key": "...",
  "client_metadata": {
    "hostname": "EXP-FALUN-01",
    "ecclesiastical_unit": "Falu pastorat",
    "site_name": "Falun församlingsexpedition",
    "site_type": "expedition",
    "contact_person": "valfritt",
    "notes": "Stationär dator i kassakontoret"
  }
}
```

**Response:**
```json
{
  "client_id": "uuid",
  "client_token": "jwt-eller-opakt",
  "initial_spec_url": "/probe/spec",
  "heartbeat_interval_seconds": 300
}
```

#### `POST /probe/heartbeat`

Anropas regelbundet. Returnerar aktuell spec (eller hint om att hämta ny).

**Request:** se 4.3 ovan

**Response:**
```json
{
  "spec_changed": true,
  "spec_url": "/probe/spec",
  "network_classification": "nkn",
  "next_heartbeat_in_seconds": 300,
  "client_actions": []
}
```

#### `GET /probe/spec`

Hämtar aktuell mätspec. Se 4.4 ovan.

#### `POST /probe/results`

Inrapportering av mätresultat (batch).

**Request:**
```json
{
  "client_id": "uuid",
  "results": [
    {
      "measurement_id": "builtin-ping-ad",
      "timestamp": "2026-05-06T10:01:23Z",
      "type": "icmp_ping",
      "target": "ad-1.intern",
      "success": true,
      "rtt_ms_min": 11.2,
      "rtt_ms_avg": 12.4,
      "rtt_ms_max": 14.1,
      "packet_loss_pct": 0
    }
  ]
}
```

**Response:**
```json
{
  "accepted": 42,
  "rejected": 0,
  "rejected_reasons": []
}
```

#### `GET /probe/peers`

Hämtar lista över peer-klienter klienten kan mäta mot. Roteras dynamiskt.

### 5.2 Validering vid inrapportering

- Resultatet måste matcha en `measurement_id` i klientens senaste utskickade spec
- Timestamp får inte vara mer än 7 dagar gammal (matchar buffringsgräns)
- Klient-token måste vara giltig och inte revoked
- Rate limiting per klient (skydd mot fel- eller missbruk)

### 5.3 Peer-tilldelning (logik)

För varje klient på NKN:
1. Identifiera klientens site (baserat på publik IP, lokalt subnät eller registreringsmetadata)
2. Plocka 3–5 peer-klienter från *andra* sites
3. Rotera urvalet med jämna mellanrum (t.ex. dagligen) för att undvika hot-spots
4. Undvik att använda klienter på samma /24 som peers
5. Anchors är alltid tillgängliga som mål för alla klienter

---

## 6. Datamodell (metadata)

```sql
-- Sites (kan delas av flera probes)
CREATE TABLE sites (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    site_type TEXT, -- expedition, kyrka, satellit, anchor
    ecclesiastical_unit TEXT,
    public_ip_ranges TEXT[], -- för NKN-klassificering
    local_ip_ranges TEXT[], -- för peer-uteslutning på samma /24
    created_at TIMESTAMPTZ
);

-- Probes
CREATE TABLE probes (
    id UUID PRIMARY KEY,
    site_id UUID REFERENCES sites(id),
    hostname TEXT,
    token_hash TEXT NOT NULL,
    role TEXT NOT NULL, -- probe, anchor
    enabled BOOLEAN NOT NULL DEFAULT true,
    last_heartbeat_at TIMESTAMPTZ,
    last_seen_public_ip TEXT,
    last_classification TEXT, -- nkn, external, unknown
    version TEXT,
    created_at TIMESTAMPTZ
);

-- Tags (probe -> taggar)
CREATE TABLE probe_tags (
    probe_id UUID REFERENCES probes(id),
    tag TEXT NOT NULL,
    source TEXT NOT NULL, -- system, user
    PRIMARY KEY (probe_id, tag)
);

-- Mål (canonical list, refereras från specs)
CREATE TABLE measurement_targets (
    id TEXT PRIMARY KEY, -- t.ex. "builtin-ping-ad"
    target TEXT NOT NULL,
    type TEXT NOT NULL,
    category TEXT NOT NULL, -- builtin, peer, user_defined
    config JSONB,
    enabled BOOLEAN NOT NULL DEFAULT true
);

-- Probe-specifik mål-tilldelning (för user_defined)
CREATE TABLE probe_targets (
    probe_id UUID REFERENCES probes(id),
    target_id TEXT REFERENCES measurement_targets(id),
    PRIMARY KEY (probe_id, target_id)
);

-- Canary-mål (intern reachability check)
CREATE TABLE canary_targets (
    id SERIAL PRIMARY KEY,
    target TEXT NOT NULL,
    description TEXT
);
```

Mätresultat lagras *inte* i metadata-DB:n – de går direkt till tidsseriedatabasen.

---

## 7. Tidsseriedata

Rekommendation: **VictoriaMetrics** (single-node, enkel drift).

Alternativ: InfluxDB 2.x (mer komplext men mer features).

Inte rekommenderat: Prometheus (pull-baserat passar dåligt för push från NAT-gömda klienter).

Metric-namngivning (Prometheus-stil):

```
nkn_ping_rtt_ms{client_id, site, stift, target, target_category}
nkn_ping_loss_pct{client_id, site, stift, target, target_category}
nkn_dns_query_ms{client_id, site, target, query_name}
nkn_dns_query_success{client_id, site, target, query_name}
nkn_http_response_ms{client_id, site, target}
nkn_http_status{client_id, site, target}
nkn_iperf_throughput_mbps{client_id, site, target, direction}
nkn_probe_up{client_id, site} (1 vid heartbeat)
```

**Retention:**
- Hög upplösning: 30 dagar
- Aggregerat (5 min): 1 år
- Aggregerat (1 h): 5 år

---

## 8. Grafana-vyer (MVP)

1. **Översikt:** antal aktiva probes, fördelning per stift, senaste 24h-trender
2. **Site-detalj:** all data för en specifik site, jämförelse mot baseline
3. **Latency-heatmap:** matrisvy probes × targets, färgkodad per RTT
4. **Tystnande probes:** lista över klienter som inte rapporterat på X minuter
5. **Anchor-status:** detaljerad vy för centrala anchors (high-frequency data)
6. **Peer-graf:** dynamisk node-graph över vilka probes som mäter mot vilka (Grafana Node Graph panel)

---

## 9. Implementationsordning (rekommenderad MVP-väg)

**Iteration 1 – End-to-end skelett (1–2 veckor)**
- FastAPI-skelett med register/heartbeat/results-endpoints
- VictoriaMetrics + Grafana via Docker Compose
- Minimal PowerShell-klient som pingar 1 mål och rapporterar in
- Grafana-dashboard som visar inrapporterade mätningar

**Iteration 2 – Mätkapacitet (1–2 veckor)**
- Implementera alla mättyper i klienten utom iperf
- Spec-distribution från koordinator
- Lokal buffring i klienten (SQLite)
- Network context check + NKN-klassificering

**Iteration 3 – Peer-mätning (1 vecka)**
- Peer-tilldelningslogik i koordinator
- Subnet-uteslutning
- Rotation
- Peer-graf-visualisering i Grafana

**Iteration 4 – User-defined och anchors (1 vecka)**
- Power-user-läge för lokala mål
- Anchor-deployment (samma kod, annan roll-flag)
- iperf-stöd

**Iteration 5 – Drift och utrullning (löpande)**
- Authenticode-signering av klient
- Installer (MSI eller PowerShell-installer)
- Dokumentation för lokala IT-ansvariga
- Pilotutrullning på 5–10 sites

---

## 10. Öppna frågor / risker

1. **Klartecken från Global Connect och central IT.** 50+ klienter som pingar och iperf:ar mot varandra kan se ut som något helt annat i deras NDR/IDS. Måste kommuniceras innan utrullning.
2. **Iperf-server-kapacitet.** Behöver dimensioneras för antalet anchors och samtidiga klienter.
3. **Brandväggsregler för canary-targets.** Internet-mål måste vara nåbara från alla NKN-sites.
4. **Klientens beteende på laptops.** Mobila enheter rör sig mellan nät – design-mässigt hanteras detta av NKN-klassificeringen, men datat kan bli rörigt.
5. **Code signing.** För produktion bör klientbinären signeras. Kräver certifikat och process.
6. **Datasäkerhet i Grafana.** Vem får se vad? Kan en lokal IT-administratör se andra siter? Bör finnas RBAC.

---

## 11. Tekniska val – sammanfattning

| Område | Val | Motivering |
|---|---|---|
| Klientspråk | PowerShell (MVP) → ev. C# senare | Lågt instegströskel, finns på alla Windows |
| Klientpaketering | Scheduled Task eller Service via NSSM | Inga extra runtime-krav |
| Koordinator | FastAPI (Python) | Etablerat hos teamet, snabb utveckling |
| Koordinator-host | Valfri Docker-host (beslut tas senare) | Stacken är portabel via docker-compose |
| Reverse proxy/TLS | Caddy | Auto-TLS, enkel konfig |
| Tidsseriedata | VictoriaMetrics | Push-vänlig, enkel drift, bra retention |
| Metadata-DB | PostgreSQL | Mogen, JSONB-stöd |
| Visualisering | Grafana | Standard, Node Graph-panel finns |
| Auth (probe) | Bearer-token (opakt) | Enkelt, revocable |
| Auth (Grafana) | OIDC mot Svenska kyrkans IDP om möjligt | Återanvänd befintlig identitet |

---

## 12. Utvecklingsmiljö och deployment

### 12.1 Strategi

Hela serversidan (koordinator, databas, tidsserier, Grafana, reverse proxy) körs som en **docker-compose-stack**. Samma stack används för:

- **Lokal utveckling** i den Linux-VM där Claude Code körs (PoC-miljö)
- **Pilotutrullning** på valfri Docker-host (intern VM, Hetzner, TERVO2 – beslut tas senare)
- **Produktion** – samma compose-fil med produktions-overrides via `docker-compose.prod.yml`

Klienten körs **inte** i compose-stacken. Den utvecklas och testas på en separat Windows-host. För att kunna iterera på koordinatorns API utan att behöva en Windows-maskin uppkopplad finns en **mock-klient i Python** som kan köras antingen lokalt eller som en valfri compose-tjänst.

### 12.2 docker-compose.yml (utveckling)

```yaml
services:
  coordinator:
    build:
      context: ./coordinator
      dockerfile: Dockerfile
    environment:
      DATABASE_URL: postgresql://nkn:nkn@postgres:5432/nkn
      VICTORIAMETRICS_URL: http://victoriametrics:8428
      LOG_LEVEL: DEBUG
      ENVIRONMENT: development
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      victoriametrics:
        condition: service_started
    volumes:
      - ./coordinator/src:/app/src  # hot-reload i dev
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: nkn
      POSTGRES_PASSWORD: nkn
      POSTGRES_DB: nkn
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./coordinator/migrations:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U nkn"]
      interval: 5s
      timeout: 5s
      retries: 5

  victoriametrics:
    image: victoriametrics/victoria-metrics:latest
    command:
      - "--storageDataPath=/storage"
      - "--retentionPeriod=90d"
      - "--httpListenAddr=:8428"
    volumes:
      - vm-data:/storage
    ports:
      - "8428:8428"  # exponeras endast i dev för debugging

  grafana:
    image: grafana/grafana:latest
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_INSTALL_PLUGINS: "" # Node Graph är inbyggd
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
      - ./grafana/dashboards:/var/lib/grafana/dashboards
    ports:
      - "3000:3000"
    depends_on:
      - victoriametrics

  # Optional: mock-klient för utveckling utan Windows-host
  mock-client:
    build:
      context: ./mock-client
    environment:
      COORDINATOR_URL: http://coordinator:8000
      MOCK_PROBES: "5"  # Simulerar 5 olika probes
      MOCK_SCENARIO: "normal"  # eller "degraded", "offline-bursts"
    profiles: ["mock"]  # körs bara med --profile mock
    depends_on:
      coordinator:
        condition: service_started

volumes:
  postgres-data:
  vm-data:
  grafana-data:
```

**Användning i utveckling:**

```bash
# Bara serversidan
docker compose up -d

# Med mock-klienter för end-to-end-test utan Windows
docker compose --profile mock up -d

# Tail logs
docker compose logs -f coordinator
```

### 12.3 docker-compose.prod.yml (overrides)

För deployment överlagras dev-konfigurationen:

```yaml
services:
  coordinator:
    build:
      target: production  # multi-stage build, ingen reload
    environment:
      LOG_LEVEL: INFO
      ENVIRONMENT: production
      DATABASE_URL: ${DATABASE_URL}
      COORDINATOR_SECRET: ${COORDINATOR_SECRET}
    volumes: []  # ingen kod-volym i prod
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
    restart: unless-stopped

  postgres:
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    restart: unless-stopped

  victoriametrics:
    ports: []  # exponeras inte direkt i prod
    command:
      - "--storageDataPath=/storage"
      - "--retentionPeriod=1y"  # längre retention i prod
      - "--httpListenAddr=:8428"
    restart: unless-stopped

  grafana:
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_SERVER_ROOT_URL: ${GRAFANA_PUBLIC_URL}
      # OIDC-konfig läggs till här när det är aktuellt
    restart: unless-stopped

  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile
      - caddy-data:/data
      - caddy-config:/config
    restart: unless-stopped
    depends_on:
      - coordinator
      - grafana

volumes:
  caddy-data:
  caddy-config:
```

**Deployment:**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Hosting-target är **medvetet inte fastställt** i denna spec – stacken är portabel mellan Hetzner Cloud, intern VM, TERVO2 eller annan Docker-host. Beslutet tas när pilotomgång planeras och kan baseras på vilka säkerhets- och dataresidens-krav som ställs av Svenska kyrkans IT.

### 12.4 PoC-flöde i utvecklings-VM

För Claude Code i Linux-VM:n blir flödet:

```bash
# 1. Skapa projektstrukturen
git init nkn-monitor && cd nkn-monitor

# 2. Bygg och starta stacken
docker compose up -d

# 3. Verifiera att alla tjänster lever
docker compose ps
curl http://localhost:8000/healthz
curl http://localhost:8428/health  # VictoriaMetrics
curl http://localhost:3000/api/health  # Grafana

# 4. Kör mock-klienten för att generera testdata
docker compose --profile mock up -d mock-client

# 5. Öppna Grafana och verifiera att data trillar in
#    http://<vm-ip>:3000  (admin/admin)
```

Mock-klienten implementerar samma API-kontrakt som den riktiga PowerShell-klienten kommer att göra, och kan simulera olika scenarier (normal drift, degraderat nät, offline-bursts) vilket låter koordinator- och dashboard-utveckling pågå parallellt med klientutvecklingen på Windows-host.

### 12.5 Klientutveckling (separat)

PowerShell-klienten utvecklas på Windows-host, helt utanför compose-stacken. Den pekas mot koordinatorns URL:

```powershell
# I dev: pekar mot Linux-VM:ens IP
$env:NKN_COORDINATOR_URL = "http://192.168.x.x:8000"
.\client\NknMonitor.psm1
```

För att klienten ska kunna nå koordinatorn över LAN behöver port 8000 vara öppen från Windows-hosten till Linux-VM:n. I produktionsdeployment går trafiken över HTTPS via Caddy på port 443.

### 12.6 Repository-struktur

```
nkn-monitor/
├── README.md
├── SPECIFICATION.md                    # detta dokument
├── docker-compose.yml                  # dev-stack (med mock-profile)
├── docker-compose.prod.yml             # prod-overrides
├── .env.example                        # exempel på env-variabler för prod
│
├── coordinator/                        # FastAPI-tjänst (i compose)
│   ├── Dockerfile                      # multi-stage: dev + production targets
│   ├── pyproject.toml
│   ├── src/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── probe.py                # /probe/* endpoints
│   │   │   └── admin.py                # admin-API (senare)
│   │   ├── classification/             # NKN-klassificering
│   │   ├── peers/                      # peer-tilldelning
│   │   ├── storage/
│   │   │   ├── postgres.py
│   │   │   └── victoriametrics.py
│   │   └── models/                     # pydantic-modeller
│   ├── migrations/                     # SQL för PostgreSQL init
│   └── tests/
│
├── mock-client/                        # Python-mock för dev (i compose)
│   ├── Dockerfile
│   ├── mock_probe.py
│   └── scenarios/
│       ├── normal.py
│       ├── degraded.py
│       └── offline_bursts.py
│
├── client/                             # PowerShell-klient (utanför compose)
│   ├── NknMonitor.psm1
│   ├── tasks/
│   │   ├── ping.ps1
│   │   ├── tcp_ping.ps1
│   │   ├── dns.ps1
│   │   ├── http.ps1
│   │   └── iperf.ps1
│   ├── installer/
│   │   └── install.ps1
│   ├── bin/
│   │   └── iperf3.exe                  # medlevereras
│   └── tests/
│
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── victoriametrics.yml
│   │   └── dashboards/
│   │       └── default.yml
│   └── dashboards/
│       ├── overview.json
│       ├── site-detail.json
│       ├── peer-graph.json
│       └── anchor-status.json
│
├── caddy/
│   └── Caddyfile                       # endast prod
│
└── docs/
    ├── operator-guide.md
    ├── pilot-checklist.md
    └── client-troubleshooting.md
```

---

## 13. Iteration 1 – konkret startpunkt för Claude Code

För att ge Claude Code en tydlig första uppgift, här är en avgränsad MVP som passar PoC-VM:en:

**Mål:** Få en mätning från mock-klient → koordinator → VictoriaMetrics → Grafana, end-to-end, i docker-compose.

**Att implementera:**

1. `docker-compose.yml` enligt 12.2
2. Coordinator med endast tre endpoints:
   - `GET /healthz` (returnerar `{"status": "ok"}`)
   - `POST /probe/register` (returnerar fast token för MVP, ingen DB-skrivning än)
   - `POST /probe/results` (validerar JSON, skriver till VictoriaMetrics)
3. Mock-klient som var 30:e sekund skickar in 1 ping-resultat (slumpat RTT 5–50 ms)
4. Grafana-dashboard som visar `nkn_ping_rtt_ms` över tid

**Definierat klart när:**
- `docker compose --profile mock up -d` startar allt
- Grafana på `:3000` visar trendgraf med data från mock-klienten
- En kommentar/README beskriver hur man verifierar

Resterande iterationer (registrering på riktigt, spec-distribution, peer-mätning, NKN-klassificering, riktig PowerShell-klient) byggs sedan ovanpå detta skelett.
