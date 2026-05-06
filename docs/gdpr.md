# NKN-Monitor – behandling av personuppgifter (GDPR)

Det här dokumentet beskriver vilka uppgifter NKN-Monitor samlar in, var
de lagras, hur länge de behålls och vilka rättigheter som finns. Avsett
för pilotutrullning inom Svenska kyrkan; ska uppdateras innan bredare
produktionssättning.

## Sammanfattning

NKN-Monitor är ett tekniskt övervakningssystem som mäter nätverkskvalitet
från klient-PC:s perspektiv. **Inga slutanvändaridentiteter samlas in.**
Datat består av:

- Tekniska identifierare (klient-UUID, hostnamn)
- Site-metadata (sitenamn, pastorat, sitetyp)
- Nätverksmätningar (RTT, paketförlust, DNS-svarstid, HTTP-status, traceroute-paths)
- Tekniska kontextfält per klient (publik IP, lokala IP-adresser, gateway, DNS-servrar, OS-version, uptime)

Inga e-postadresser, AD-användarnamn, dokumentinnehåll eller liknande
samlas in.

## Vad som lagras och var

| Data | Lagras i | Retention |
|------|----------|-----------|
| Probe-registrering (klient-UUID, sitemetadata, hostnamn, publik IP, lokala IP, klassificering, version) | SQLite (`/data/coordinator.db`) | Tills probe avregistreras eller sweepas (default >24 h utan heartbeat) |
| Bearer-tokens per probe (SHA-256-hash) | SQLite | Samma som ovan; klartext finns bara hos klienten |
| Mätresultat (RTT, paketförlust, DNS-svar, HTTP-status, peer-mätningar, canary-status) | VictoriaMetrics (`/data/vm/`) | 90 dagar full upplösning (konfigurerbart i `--retentionPeriod`) |
| Traceroute-paths (hop-IPs och hostnames) | SQLite (`/data/coordinator.db`, tabell `traceroute_paths`) | Senaste 50 körningar per (probe, mått) |
| Coordinator-konfiguration (mått, registreringsnycklar, NKN-ranges) | YAML-fil (`/data/config.yaml`) | Tills explicit raderad |
| Grafana-state (dashboards, datasources, användare) | SQLite (`/data/grafana/grafana.db`) | Tills explicit raderad |
| Klientens lokala buffer vid offline | JSONL-fil på klienten (`%LOCALAPPDATA%\NKN-Monitor\buffer.jsonl`) | 7 dagar |

## Rättslig grund

Behandlingen sker inom ramen för **berättigat intresse** (GDPR art. 6.1.f):
övervakning av Svenska kyrkans interna nätverk är en driftnödvändig
funktion som inte påverkar de registrerades grundläggande rättigheter
nämnvärt.

Tekniska identifierare som **publik IP** och **hostnamn** kan i teorin
kopplas till en enskild dator och därmed indirekt till en användare i
ett pastorats datormiljö. Det är därför inkluderat i denna bedömning
trots att inga personnamn lagras.

## Dataminimering

- Inga AD-användarnamn, e-postadresser eller domäninloggade användares
  identifierare loggas av klienten (jfr `Get-NetworkContext` i
  `client/NknMonitor.ps1`).
- `domain_membership`-fältet i heartbeat speglar bara datorns AD-domän,
  inte den inloggade användaren.
- Klientens hostnamn är **valfritt** vid registrering. Lokal
  IT-administratör kan välja att lämna det blankt eller använda ett
  pseudonym (t.ex. `EXP-FALUN-01`).
- IP-adresser som lagras är de tekniska som behövs för peer-mätning
  och NKN-klassificering.

## De registrerades rättigheter

Eftersom inga slutanvändare identifieras direkt finns ingen bunden
rättighetsutövning per användare. Däremot:

- **Rätt till radering av en specifik probe:** logga in i admin-UI på
  `/admin/`, hitta proben i tabellen och radera den (kräver att vi
  bygger ut admin-UI – nuvarande version har bara sweep av döda
  probes; per-probe-delete är på todo-listan).
- **Rätt till information:** detta dokument samt `SPECIFICATION.md` §3.4.

## Dataöverföringar

- Coordinator och Grafana körs i Sverige (på Unraid-host i pilotfasen,
  Hetzner Helsinki/Falkenstein i framtid).
- Inga uppgifter delas med tredje part.
- Klienten gör en publik IP-lookup mot externa tjänster
  (`api.ipify.org`, `ifconfig.me`, `icanhazip.com`) som en del av
  network context check. Dessa anrop avslöjar IP-adressen för respektive
  tjänst men är nödvändiga för NKN-klassificeringen. Kan stängas av
  framtid genom att låta klassificering ske enbart baserat på lokala
  IP:n.

## Säkerhet

- Klient-tokens hashas (SHA-256) på serversidan. Klartext finns bara
  hos respektive klient (`%LOCALAPPDATA%\NKN-Monitor\config.json`).
- Admin-UI är skyddat med HTTP Basic-auth (`ADMIN_TOKEN`). Default-token
  ska bytas innan publik exponering.
- All extern trafik bör gå över TLS via reverse proxy (nginx-proxy-manager
  eller Caddy med autoTLS).
- VictoriaMetrics-instansen har **ingen auth** och får aldrig exponeras
  publikt – den körs internt i containern och nås bara av coordinator
  och Grafana.

## Loggar

Coordinator loggar HTTP-anrop på INFO/DEBUG-nivå inklusive klient-IPs.
För GDPR-syfte rekommenderas att logg-retention begränsas till 30
dagar. Just nu skrivs loggar bara till stdout (Docker-logg) och
roteras enligt Docker-konfigurationen.

## Att göra innan bredare utrullning

- Per-probe delete-knapp i admin-UI (idag finns sweep men inte explicit
  delete på enskild probe-id)
- Logg-rotation och retention-policy för Docker-loggar
- Eventuell anonymisering av publik IP i Grafana-dashboards (hash eller /24-aggregat)
- Avtal mellan instansansvarig (t.ex. pastorat) och Svenska kyrkan
  centralt om vem som är personuppgiftsansvarig
- Rutin för incidentrapportering vid eventuell dataläcka
