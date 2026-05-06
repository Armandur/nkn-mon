# NKN-Monitor PowerShell-klient

Probe-klient som körs på Windows-host och rapporterar in nätmätningar till en
NKN-Monitor coordinator.

## Status

**v0.1 (Iteration 2 leverans 1):**

- Registrering mot `/probe/register` (token sparas i `%LOCALAPPDATA%\NKN-Monitor\config.json`)
- Hämtar mätspec från `/probe/spec`
- Stöder `icmp_ping` via `Test-Connection`
- Skickar resultat till `/probe/results`

**v0.3 (Iteration 2 leverans 2):**
- Alla mättyper: icmp_ping, tcp_ping, dns_query, http_get
- Heartbeat + network context check (publik IP, gateway, DNS, canaries)
- Per-mått-intervall (respekterar `interval_seconds` i specen)
- 401-handling: re-registrerar automatiskt om token blivit ogiltig
- Lokal JSONL-buffring vid offline
  (`%LOCALAPPDATA%\NKN-Monitor\buffer.jsonl`, 7 dagars retention)
- Flushas automatiskt vid uppstart och efter varje lyckad rapport

**Inte implementerat ännu:**
- iperf3 (Iteration 4)
- Scheduled Task-installer
- Klient-uppdateringsmekanism via heartbeat-svar

## Krav

- Windows 10/11 med PowerShell 5.1 eller PowerShell 7+
- Nätverksåtkomst till coordinator-URLen

## Snabbstart (utvecklingstest)

I VMen där coordinatorn körs (Linux): säkerställ att stacken är uppe (`docker compose up -d`)
och att port 8200 är öppen. På Windows-hosten:

```powershell
# Ett varv (smoke-test, avslutas efter en runda)
.\NknMonitor.ps1 -CoordinatorUrl http://ubuntu-ai:8200 -Once

# Loop, default-defaulter
$env:NKN_COORDINATOR_URL = "http://ubuntu-ai:8200"
.\NknMonitor.ps1
```

Default-värden (kan överskuggas med parametrar eller env):

| Parameter | Default |
|-----------|---------|
| `-CoordinatorUrl` | `$env:NKN_COORDINATOR_URL` eller `http://localhost:8200` |
| `-RegistrationKey` | `$env:NKN_REGISTRATION_KEY` eller `dev-registration-key` |
| `-SiteName` | `$env:COMPUTERNAME` |
| `-EcclesiasticalUnit` | tom |
| `-SiteType` | `expedition` |
| `-ConfigPath` | `%LOCALAPPDATA%\NKN-Monitor\config.json` |

## Felsökning

- **403 Ogiltig registreringsnyckel:** matcha `-RegistrationKey` mot
  `registration_keys` i `coordinator/config.yaml`.
- **401 vid spec/results:** lokala configfilen är skadad eller token är revoked.
  Radera `%LOCALAPPDATA%\NKN-Monitor\config.json` och kör igen.
- **Ping ger 0 resultat:** brandvägg, eller målet är inte nåbart från klienten
  (t.ex. `ad-1.intern` finns bara i NKN). Bevara mätningen i specen ändå - då
  syns det som `nkn_ping_success=0` i Grafana.

## Buggar och förbättringar

Notera dessa om du hittar dem:

- `Test-Connection`-property heter olika i PS 5.1 (`ResponseTime`) och PS 7
  (`Latency`). Skriptet hanterar båda men har inte regressionstestats brett.
- `Invoke-RestMethod` på PS 5.1 skickar default i ISO-8859-1 vilket bryter
  FastAPI:s JSON-parser. Wrappen `Invoke-NknJson` konverterar till UTF-8-bytes
  med explicit `charset=utf-8` så det funkar för båda PS-versioner.
- DnsClient-modulen förladdas vid skriptstart (`Import-Module DnsClient`)
  så första `Resolve-DnsName`-anropet inte beskattas av ~1 sek modulladdning.
- Buffer-fil sparas under `%LOCALAPPDATA%\NKN-Monitor\buffer.jsonl` – pathen
  loggas vid uppstart för diagnostik.
