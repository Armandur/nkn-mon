# NKN-Monitor – todo

Levande lista över vad som behöver göras härnäst. Färdiga iterationer dokumenteras i [`README.md`](./README.md) och [`SPECIFICATION.md`](./SPECIFICATION.md).

## Mätningar

- [ ] **`traceroute` som mättyp** – via `Test-NetConnection -TraceRoute` på Windows. Värdefullt för att se var i kedjan en fördröjning eller bortfall sker, inte bara att den finns. Nytt metric `nkn_traceroute_hops{...}` som lista, plus per-hop-RTT om tillgängligt. Användning: rikta mot kritiska mål och mot peers när RTT plötsligt sticker upp.
- [ ] **`iperf3`** för bandbreddsmätning mot anchors (Iteration 4 enligt spec §4.5). Kräver iperf3.exe medlevereras i klienten + anchor-server.
- [ ] **`tcp_ping` mot fler portar** (443, 80) som health-check oberoende av HTTP-svar.

## Klient

- [ ] **Scheduled Task-installer** (`client/install.ps1`) som kopierar skriptet till `%PROGRAMDATA%\NKN-Monitor` och registrerar Task som triggar vid uppstart eller logon. Behövs innan pilotutrullning.
- [ ] **`-LogFile`-parameter** så Scheduled Task-körningar skriver till fil för felsökning.
- [ ] **Klient-uppdateringsmekanism** – heartbeat-svar talar om "ny version finns", klienten laddar ner och byter ut sig själv. Spec §4.7.
- [ ] **Authenticode-signering** av klientbinär innan bredare utrullning.

## Coordinator

- [ ] **User-defined mätningar per probe** (Iteration 4) – local admin lägger till egna mål för en specifik probe via admin-UI:t.
- [ ] **Site-koncept** – flera probes per site, dela metadata (NKN-ranges, koordinater för geomap).
- [ ] **Probe-koordinater (lat/lng)** för geomap-vy.
- [ ] **Wipe & re-register-flagga** – admin kan tvinga en probe att registrera om sig vid nästa heartbeat.
- [ ] **Audit-logg** för admin-config-ändringar (vem ändrade vad när).

## Visualisering

- [ ] **Geomap** med sites på Sverigekarta (kräver lat/lng per site).
- [ ] **Peer-graf** (Node Graph-panel) – kräver Grafana-plugin eller datasource som kan leverera nodes+edges.
- [ ] **Anchor-fokuserad dashboard** – high-frequency vy mot anchors (spec §8 punkt 5).

## Drift / produktion

- [ ] **Caddy-konfig** med autoTLS i `caddy/Caddyfile` + reverse proxy mot coordinator + Grafana.
- [ ] **OIDC mot Svenska kyrkans IDP** för Grafana-login (spec §11).
- [ ] **Retention-policys** dokumenterade och konfigurerade i VictoriaMetrics + Grafana.
- [ ] **GitHub Actions** som bygger image till `ghcr.io/armandur/nkn-mon`.
- [ ] **GDPR-dokument** – vad samlas in, var lagras det, vem har tillgång (utgår från §3.4).
- [ ] **Backup av SQLite + VM-data** i prod.

## Kvalitet

- [ ] **Pytest-suite** för coordinator (config, peers, classification, vm.build_lines).
- [ ] **Pester-tester** för PowerShell-klienten (mätfunktioner, buffer).
- [ ] **CI-byggen** av Docker-image vid push.

## Småfixar

- [x] ~~Filtrera bort virtuella nätadaptrar i klientens local_ipv4~~
- [x] ~~Anchor-stöd i peer-tilldelningen~~
- [ ] Snyggare ålders-format i admin-UI (t.ex. "för 3 minuter sedan" på svenska).
- [ ] `last_local_ipv4` ska visas i probes-tabellen i admin-UI.
- [ ] Sweep-knappen ska ha en bekräftelse-toast efter klar (idag bara sätter status).
