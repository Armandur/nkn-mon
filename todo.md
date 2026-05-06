# NKN-Monitor – todo

Levande lista över vad som behöver göras härnäst. Färdiga iterationer dokumenteras i [`README.md`](./README.md) och [`SPECIFICATION.md`](./SPECIFICATION.md).

## Mätningar

- [x] ~~Alla baseline-mättyper (icmp_ping, tcp_ping, dns_query, http_get)~~
- [x] ~~`traceroute` som mättyp med reverse-DNS för hops i klientens nät~~
- [ ] **`iperf3`** för bandbreddsmätning mot anchors (Iteration 4 enligt spec §4.5). Kräver iperf3.exe medlevereras i klienten + anchor-server.
- [ ] **`tcp_ping` mot fler portar** (443, 80) som health-check oberoende av HTTP-svar.

## Klient

- [x] ~~Alla mättyper i samma klient~~
- [x] ~~Heartbeat + network context check~~
- [x] ~~NKN-klassificering på serversidan~~
- [x] ~~Per-mått-intervall (klienten respekterar `interval_seconds`)~~
- [x] ~~401-handling: re-registrerar automatiskt vid ogiltig token~~
- [x] ~~Lokal JSONL-buffring vid offline (7 d retention)~~
- [x] ~~Reverse-DNS för traceroute-hops i klientens nät~~
- [x] ~~Filtrering av virtuella nätadaptrar (Tailscale, Hyper-V, Docker, m.fl.)~~
- [ ] **Scheduled Task-installer** (`client/install.ps1`) som kopierar skriptet till `%PROGRAMDATA%\NKN-Monitor` och registrerar Task som triggar vid uppstart eller logon. Behövs innan pilotutrullning. (skippad enligt användarens beslut tills vidare)
- [ ] **`-LogFile`-parameter** så Scheduled Task-körningar skriver till fil för felsökning.
- [ ] **Klient-uppdateringsmekanism** – heartbeat-svar talar om "ny version finns", klienten laddar ner och byter ut sig själv. Spec §4.7.
- [ ] **Authenticode-signering** av klientbinär innan bredare utrullning.

## Coordinator

- [x] ~~Centralt config med admin-UI (formulär + rå YAML)~~
- [x] ~~SQLite för probes + traceroute-paths~~
- [x] ~~Anchor-stöd: `role`-kolumn, peer-tilldelning prioriterar anchors~~
- [x] ~~Sweep av döda probes via admin-API~~
- [ ] **Wipe & re-register-flagga** – admin kan tvinga en specifik probe att registrera om sig vid nästa heartbeat.
- [ ] **Per-probe delete** i admin-UI (idag finns sweep men inte delete på enskild id).
- [ ] **User-defined mätningar per probe** (Iteration 4) – local admin lägger till egna mål för en specifik probe via admin-UI:t.
- [ ] **Site-koncept** – flera probes per site, dela metadata (NKN-ranges, koordinater för geomap).
- [ ] **Probe-koordinater (lat/lng)** för geomap-vy.
- [ ] **Audit-logg** för admin-config-ändringar (vem ändrade vad när).

## Visualisering

- [x] ~~Översikt-dashboard~~
- [x] ~~Site-detalj med variable-väljare~~
- [x] ~~Latency-matris (sorterad tabell)~~
- [x] ~~Tystnande probes-vy~~
- [x] ~~SLA / uptime-tabell~~
- [x] ~~Peer-graf (Node Graph via Infinity-plugin)~~
- [x] ~~Traceroute-graf (Node Graph) med site-filter och hostname per hop~~
- [ ] **Geomap** med sites på Sverigekarta (kräver lat/lng per site).
- [ ] **Anchor-fokuserad dashboard** – high-frequency vy mot anchors (spec §8 punkt 5).

## Drift / produktion

- [x] ~~Single-image-Dockerfile (Grafana + VM + coordinator under s6-overlay)~~
- [x] ~~GitHub Actions bygger till `ghcr.io/armandur/nkn-mon`~~
- [x] ~~GDPR-dokumentation (`docs/gdpr.md`)~~
- [x] ~~Unraid-deployment-guide (`docs/unraid-deployment.md`)~~
- [ ] **Caddy-konfig** med autoTLS i `caddy/Caddyfile` + reverse proxy mot coordinator + Grafana för Hetzner-prod (på Unraid-pilot används befintlig NPM).
- [ ] **OIDC mot Svenska kyrkans IDP** för Grafana-login (spec §11).
- [ ] **Retention-policys** dokumenterade och konfigurerade i VictoriaMetrics + Grafana (just nu: VM 90d, ingen downsampling).
- [ ] **Backup av SQLite + VM-data** i prod.
- [ ] **Logg-rotation** för Docker-loggar (Docker default räcker oftast men dokumentera).

## Kvalitet

- [ ] **Pytest-suite** för coordinator (config, peers, classification, vm.build_lines, storage).
- [ ] **Pester-tester** för PowerShell-klienten (mätfunktioner, buffer, json-encoding).

## Småfixar

- [x] ~~Filtrera bort virtuella nätadaptrar i klientens local_ipv4~~
- [x] ~~Anchor-stöd i peer-tilldelningen~~
- [x] ~~Snyggare ålders-format på svenska i admin-UI~~
- [x] ~~`last_local_ipv4` i probes-tabellen i admin-UI~~
- [x] ~~Färgkodade peer-graph edges efter RTT~~
- [x] ~~Anchor-noder större i peer-grafen~~
- [x] ~~Site-namn istället för IP i peer-mesh-tabell (Från / Från-IP / Till / Till-IP)~~
- [x] ~~"Senaste icmp-status" som tabell istället för stat (för många celler annars)~~
- [x] ~~Formulär-UI för config + rå YAML-tab~~
- [x] ~~Hjälptexter på varje config-sektion + per-mättyp-detaljer~~
- [ ] Sweep-knappens bekräftelse-toast (idag bara sätter status).
- [ ] Snyggare ålders-format för traceroute-historik (samma som probes-tabellen).
