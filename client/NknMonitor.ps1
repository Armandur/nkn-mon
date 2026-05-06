<#
.SYNOPSIS
    NKN-Monitor probe-klient v0.1 (PowerShell).

.DESCRIPTION
    Registrerar maskinen mot en NKN-Monitor coordinator, hämtar mätspec och
    rapporterar in icmp_ping-resultat enligt spec. Defaultvärden plockas från
    miljö och hostname så att skriptet kan köras utan parametrar för enklast
    möjliga test.

    I Iteration 2 stöds bara icmp_ping. Andra mättyper i specen ignoreras
    tills stöd läggs till.

.PARAMETER CoordinatorUrl
    Bas-URL till coordinator (t.ex. http://ubuntu-ai:8200). Default: env-variabeln
    NKN_COORDINATOR_URL eller http://localhost:8200.

.PARAMETER RegistrationKey
    Registreringsnyckel som matchar coordinatorns config. Default: env-variabeln
    NKN_REGISTRATION_KEY eller "dev-registration-key".

.PARAMETER SiteName
    Site-namn som visas i Grafana. Default: $env:COMPUTERNAME.

.PARAMETER EcclesiasticalUnit
    Pastorat/stift. Default: tomt.

.PARAMETER SiteType
    "expedition" / "kyrka" / "satellit" / "anchor". Default: "expedition".

.PARAMETER ConfigPath
    Var lokala konfigfilen sparas. Default: %LOCALAPPDATA%\NKN-Monitor\config.json

.PARAMETER Once
    Kör en runda mätningar och avsluta (för smoke-test). Annars loop.

.PARAMETER NonInteractive
    Hoppa över interaktiva prompter. Använd vid Scheduled Task-körning där
    skriptet kör utan terminal. Default-värden används utan frågor.

.EXAMPLE
    # Interaktivt, med prompter för metadata vid första registrering
    .\NknMonitor.ps1 -CoordinatorUrl http://ubuntu-ai:8200

.EXAMPLE
    # Smoke-test, en runda
    .\NknMonitor.ps1 -Once

.EXAMPLE
    # Scheduled Task-läge, inga frågor
    .\NknMonitor.ps1 -NonInteractive
#>
[CmdletBinding()]
param(
    [string]$CoordinatorUrl = $(if ($env:NKN_COORDINATOR_URL) { $env:NKN_COORDINATOR_URL } else { "http://localhost:8200" }),
    [string]$RegistrationKey = $(if ($env:NKN_REGISTRATION_KEY) { $env:NKN_REGISTRATION_KEY } else { "dev-registration-key" }),
    [string]$SiteName = "",
    [string]$EcclesiasticalUnit = "",
    [string]$SiteType = "",
    [string]$Notes = "",
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA "NKN-Monitor\config.json"),
    [switch]$Once,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$CoordinatorUrl = $CoordinatorUrl.TrimEnd("/")

# Warmup: ladda DnsClient-modulen i förväg så att första Resolve-DnsName i
# mätloopen inte beskattas av modulladdning (~1 sekund i kalla körningar).
Import-Module DnsClient -ErrorAction SilentlyContinue | Out-Null

function Write-NknLog {
    param([string]$Level, [string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "$ts $Level $Message"
}

function Read-LocalConfig {
    if (Test-Path $ConfigPath) {
        try {
            return Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
        } catch {
            Write-NknLog "WARN" "Kunde inte läsa $ConfigPath - registrerar om: $_"
        }
    }
    return $null
}

function Save-LocalConfig {
    param([pscustomobject]$Config)
    $dir = Split-Path -Parent $ConfigPath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $Config | ConvertTo-Json -Depth 10 | Set-Content -Path $ConfigPath -Encoding UTF8
}

function Read-WithDefault {
    param([string]$Prompt, [string]$Default)
    $hint = if ($Default) { " [$Default]" } else { "" }
    $answer = Read-Host -Prompt "$Prompt$hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer
}

function Read-WithChoices {
    param([string]$Prompt, [string[]]$Choices, [string]$Default)
    $list = ($Choices | ForEach-Object { if ($_ -eq $Default) { "[$_]" } else { $_ } }) -join " / "
    while ($true) {
        $answer = Read-Host -Prompt "$Prompt ($list)"
        if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
        if ($Choices -contains $answer) { return $answer }
        Write-Host "  Ogiltigt val. Välj något av: $($Choices -join ', ')" -ForegroundColor Yellow
    }
}

function Get-RegistrationMetadata {
    # Default-värden baserade på maskin + parametrar.
    $defaults = @{
        SiteName = if ($SiteName) { $SiteName } else { $env:COMPUTERNAME }
        EcclesiasticalUnit = $EcclesiasticalUnit
        SiteType = if ($SiteType) { $SiteType } else { "expedition" }
        Notes = $Notes
    }

    if ($NonInteractive) {
        return $defaults
    }

    Write-Host ""
    Write-Host "=== NKN-Monitor: registrering ===" -ForegroundColor Cyan
    Write-Host "Tryck Enter för att acceptera default i hakparenteser."
    Write-Host ""

    $defaults.SiteName = Read-WithDefault "Site-namn (visas i Grafana)" $defaults.SiteName
    $defaults.EcclesiasticalUnit = Read-WithDefault "Pastorat/stift" $defaults.EcclesiasticalUnit
    $defaults.SiteType = Read-WithChoices "Site-typ" @("expedition", "kyrka", "satellit", "anchor") $defaults.SiteType
    $defaults.Notes = Read-WithDefault "Anteckningar (valfritt)" $defaults.Notes

    Write-Host ""
    return $defaults
}

function Invoke-NknJson {
    <#
        Wrappar Invoke-RestMethod med UTF-8-encodad body. Behövs eftersom
        Invoke-RestMethod på PowerShell 5.1 skickar strängar i ISO-8859-1
        som default vilket bryter FastAPI:s JSON-parser.
    #>
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Headers = @{},
        [object]$Body
    )
    $params = @{
        Method = $Method
        Uri = $Uri
        Headers = $Headers
        ContentType = "application/json; charset=utf-8"
        UseBasicParsing = $true
    }
    if ($Body) {
        $json = $Body | ConvertTo-Json -Depth 8 -Compress
        $params.Body = [System.Text.Encoding]::UTF8.GetBytes($json)
    }
    return Invoke-RestMethod @params
}

function Register-Probe {
    $meta = Get-RegistrationMetadata

    $payload = @{
        registration_key = $RegistrationKey
        client_metadata = @{
            hostname = $env:COMPUTERNAME
            site_name = $meta.SiteName
            ecclesiastical_unit = $meta.EcclesiasticalUnit
            site_type = $meta.SiteType
            notes = $meta.Notes
        }
    }

    Write-NknLog "INFO" "Registrerar mot $CoordinatorUrl med site_name='$($meta.SiteName)'"
    $resp = Invoke-NknJson -Method Post -Uri "$CoordinatorUrl/probe/register" -Body $payload

    $cfg = [pscustomobject]@{
        coordinator_url = $CoordinatorUrl
        client_id = $resp.client_id
        client_token = $resp.client_token
        site_name = $meta.SiteName
        registered_at = (Get-Date).ToString("o")
    }
    Save-LocalConfig -Config $cfg
    Write-NknLog "INFO" "Registrerad: client_id=$($resp.client_id)"
    return $cfg
}

function Get-Spec {
    param([string]$Token)
    $headers = @{ Authorization = "Bearer $Token" }
    return Invoke-RestMethod -Method Get -Uri "$CoordinatorUrl/probe/spec" -Headers $headers
}

function Get-MeasurementExtra {
    param([pscustomobject]$Measurement, [string]$Key, $Default = $null)
    if ($Measurement.PSObject.Properties["extra"] -and `
        $Measurement.extra -and `
        $Measurement.extra.PSObject.Properties[$Key]) {
        return $Measurement.extra.$Key
    }
    return $Default
}

function New-NknResult {
    param([pscustomobject]$Measurement, [string]$Type)
    return [ordered]@{
        measurement_id = $Measurement.id
        timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        type = $Type
        target = $Measurement.target
        success = $false
    }
}

function Invoke-IcmpPing {
    param([pscustomobject]$Measurement)

    $packetCount = [int](Get-MeasurementExtra -Measurement $Measurement -Key "packet_count" -Default 4)
    $target = $Measurement.target
    $result = New-NknResult -Measurement $Measurement -Type "icmp_ping"
    $result.rtt_ms_min = $null
    $result.rtt_ms_avg = $null
    $result.rtt_ms_max = $null
    $result.packet_loss_pct = 100.0

    try {
        $pings = Test-Connection -ComputerName $target -Count $packetCount -ErrorAction Stop
        $rtts = @($pings | ForEach-Object {
            if ($_.PSObject.Properties["Latency"]) { $_.Latency }
            elseif ($_.PSObject.Properties["ResponseTime"]) { $_.ResponseTime }
            else { $null }
        } | Where-Object { $_ -ne $null })

        if ($rtts.Count -gt 0) {
            $result.success = $true
            $result.rtt_ms_min = [double]($rtts | Measure-Object -Minimum).Minimum
            $result.rtt_ms_avg = [double]($rtts | Measure-Object -Average).Average
            $result.rtt_ms_max = [double]($rtts | Measure-Object -Maximum).Maximum
            $loss = ($packetCount - $rtts.Count) / $packetCount * 100.0
            $result.packet_loss_pct = [double]$loss
        }
    } catch {
        Write-NknLog "WARN" "icmp_ping mot $target misslyckades: $_"
    }
    return [pscustomobject]$result
}

function Invoke-TcpPing {
    param([pscustomobject]$Measurement)

    $port = [int](Get-MeasurementExtra -Measurement $Measurement -Key "port" -Default 0)
    $timeoutMs = [int](Get-MeasurementExtra -Measurement $Measurement -Key "timeout_ms" -Default 5000)
    $target = $Measurement.target
    $result = New-NknResult -Measurement $Measurement -Type "tcp_ping"
    $result.rtt_ms = $null

    if ($port -le 0) {
        Write-NknLog "WARN" "tcp_ping mot $target saknar port i config"
        return [pscustomobject]$result
    }

    $client = $null
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($target, $port, $null, $null)
        $waited = $async.AsyncWaitHandle.WaitOne($timeoutMs, $false)
        $sw.Stop()
        if ($waited -and $client.Connected) {
            $client.EndConnect($async)
            $result.success = $true
            $result.rtt_ms = [double]$sw.Elapsed.TotalMilliseconds
        }
    } catch {
        Write-NknLog "WARN" "tcp_ping mot ${target}:${port} misslyckades: $_"
    } finally {
        if ($client) { $client.Close() }
    }
    return [pscustomobject]$result
}

function Invoke-DnsQuery {
    param([pscustomobject]$Measurement)

    $server = $Measurement.target
    $queryName = [string](Get-MeasurementExtra -Measurement $Measurement -Key "query_name" -Default "")
    $queryType = [string](Get-MeasurementExtra -Measurement $Measurement -Key "query_type" -Default "A")
    $result = New-NknResult -Measurement $Measurement -Type "dns_query"
    $result.rtt_ms = $null
    $result.dns_records = 0

    if (-not $queryName) {
        Write-NknLog "WARN" "dns_query mot $server saknar query_name i config"
        return [pscustomobject]$result
    }

    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $records = Resolve-DnsName -Name $queryName -Server $server -Type $queryType `
            -DnsOnly -NoHostsFile -ErrorAction Stop
        $sw.Stop()
        $result.success = $true
        $result.rtt_ms = [double]$sw.Elapsed.TotalMilliseconds
        $result.dns_records = @($records).Count
    } catch {
        Write-NknLog "WARN" "dns_query mot $server ($queryName/$queryType) misslyckades: $_"
    }
    return [pscustomobject]$result
}

function Invoke-HttpGet {
    param([pscustomobject]$Measurement)

    $url = $Measurement.target
    $expectStatus = [int](Get-MeasurementExtra -Measurement $Measurement -Key "expect_status" -Default 0)
    $timeoutSec = [int](Get-MeasurementExtra -Measurement $Measurement -Key "timeout_seconds" -Default 10)
    $result = New-NknResult -Measurement $Measurement -Type "http_get"
    $result.http_status = 0
    $result.http_total_ms = $null
    $result.http_ttfb_ms = $null

    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $resp = Invoke-WebRequest -Uri $url -Method Get -UseBasicParsing `
            -TimeoutSec $timeoutSec -ErrorAction Stop
        $sw.Stop()
        $result.http_status = [int]$resp.StatusCode
        $result.http_total_ms = [double]$sw.Elapsed.TotalMilliseconds
        if ($expectStatus -gt 0) {
            $result.success = ($result.http_status -eq $expectStatus)
        } else {
            $result.success = ($result.http_status -ge 200 -and $result.http_status -lt 400)
        }
    } catch [System.Net.WebException] {
        if ($sw.IsRunning) { $sw.Stop() }
        $result.http_total_ms = [double]$sw.Elapsed.TotalMilliseconds
        if ($_.Exception.Response) {
            $result.http_status = [int]$_.Exception.Response.StatusCode
        }
        Write-NknLog "WARN" "http_get mot $url misslyckades (status $($result.http_status)): $($_.Exception.Message)"
    } catch {
        Write-NknLog "WARN" "http_get mot $url misslyckades: $_"
    }
    return [pscustomobject]$result
}

function Invoke-Measurement {
    param([pscustomobject]$Measurement)
    switch ($Measurement.type) {
        "icmp_ping" { return Invoke-IcmpPing -Measurement $Measurement }
        "tcp_ping"  { return Invoke-TcpPing  -Measurement $Measurement }
        "dns_query" { return Invoke-DnsQuery -Measurement $Measurement }
        "http_get"  { return Invoke-HttpGet  -Measurement $Measurement }
        default {
            Write-NknLog "WARN" "Okänd mättyp '$($Measurement.type)' för $($Measurement.id) - ignoreras"
            return $null
        }
    }
}

function Send-Results {
    param([pscustomobject]$Config, [object[]]$Results)
    if (-not $Results -or $Results.Count -eq 0) { return }
    $payload = @{
        client_id = $Config.client_id
        results = $Results
    }
    $headers = @{ Authorization = "Bearer $($Config.client_token)" }
    $resp = Invoke-NknJson -Method Post -Uri "$CoordinatorUrl/probe/results" -Headers $headers -Body $payload
    Write-NknLog "INFO" "Skickade $($Results.Count) resultat: accepted=$($resp.accepted) rejected=$($resp.rejected)"
}

function Get-PublicIp {
    foreach ($url in @("https://api.ipify.org", "https://ifconfig.me/ip", "https://ipv4.icanhazip.com")) {
        try {
            $ip = (Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing).Content.Trim()
            if ($ip -match "^\d+\.\d+\.\d+\.\d+$") { return $ip }
        } catch {}
    }
    return $null
}

function Get-NetworkContext {
    param([object[]]$CanaryTargets)

    $publicIp = Get-PublicIp

    $localIPv4 = @()
    try {
        $localIPv4 = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.PrefixOrigin -ne "WellKnown" } |
            Select-Object -ExpandProperty IPAddress)
    } catch {}

    $gateway = $null
    try {
        $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
            Sort-Object RouteMetric | Select-Object -First 1
        if ($route) { $gateway = $route.NextHop }
    } catch {}

    $dnsServers = @()
    try {
        $dnsServers = @(Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.ServerAddresses } |
            ForEach-Object { $_.ServerAddresses } |
            Sort-Object -Unique)
    } catch {}

    $domain = $env:USERDNSDOMAIN

    $canaryResults = @()
    foreach ($c in $CanaryTargets) {
        if (-not $c -or -not $c.target) { continue }
        $cr = [ordered]@{
            target = $c.target
            reachable = $false
            rtt_ms = $null
        }
        try {
            $ping = Test-Connection -ComputerName $c.target -Count 1 -ErrorAction Stop
            $cr.reachable = $true
            if ($ping.PSObject.Properties["Latency"]) { $cr.rtt_ms = [double]$ping.Latency }
            elseif ($ping.PSObject.Properties["ResponseTime"]) { $cr.rtt_ms = [double]$ping.ResponseTime }
        } catch {}
        $canaryResults += [pscustomobject]$cr
    }

    return [pscustomobject]@{
        public_ip = $publicIp
        local_ipv4 = $localIPv4
        default_gateway = $gateway
        dns_servers = $dnsServers
        domain_membership = $domain
        canary_results = $canaryResults
    }
}

function Get-HostInfo {
    $info = [ordered]@{
        os = "$([System.Environment]::OSVersion.VersionString)"
        uptime_hours = $null
    }
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $info.uptime_hours = [double]((Get-Date) - $os.LastBootUpTime).TotalHours
        $info.os = "$($os.Caption) $($os.Version)"
    } catch {}
    return [pscustomobject]$info
}

function Send-Heartbeat {
    param([pscustomobject]$Config, [object[]]$CanaryTargets)

    $netCtx = Get-NetworkContext -CanaryTargets $CanaryTargets
    $hostInfo = Get-HostInfo

    $payload = @{
        timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        version = "0.3.0"
        network_context = $netCtx
        host_info = $hostInfo
    }
    $headers = @{ Authorization = "Bearer $($Config.client_token)" }
    $resp = Invoke-NknJson -Method Post -Uri "$CoordinatorUrl/probe/heartbeat" -Headers $headers -Body $payload
    Write-NknLog "INFO" "Heartbeat: classification=$($resp.network_classification), public_ip=$($netCtx.public_ip), canaries=$($netCtx.canary_results.Count), nästa om $($resp.next_heartbeat_in_seconds)s"
    return $resp
}

# --- Huvudloop -------------------------------------------------------------

$config = Read-LocalConfig
if (-not $config -or -not $config.client_token -or $config.coordinator_url -ne $CoordinatorUrl) {
    $config = Register-Probe
}

$specCacheUntil = [datetime]::MinValue
$specMeasurements = @()
$canaryTargets = @()
$lastRun = @{}
$nextHeartbeatAt = [datetime]::MinValue
$heartbeatIntervalSeconds = 60   # initial; uppdateras från heartbeat-svar
$tickSeconds = 5                 # huvudloopens vakna-intervall

while ($true) {
    $now = Get-Date

    if ($now -ge $specCacheUntil) {
        try {
            $spec = Get-Spec -Token $config.client_token
            $supportedTypes = @("icmp_ping", "tcp_ping", "dns_query", "http_get")
            $specMeasurements = @($spec.measurements | Where-Object { $supportedTypes -contains $_.type })
            $canaryTargets = @()
            if ($spec.PSObject.Properties["canary_targets"] -and $spec.canary_targets) {
                $canaryTargets = @($spec.canary_targets)
            }

            $newCacheUntil = $null
            if ($spec.PSObject.Properties["valid_until"] -and $spec.valid_until) {
                try { $newCacheUntil = [DateTime]::Parse($spec.valid_until).ToLocalTime() } catch {}
            }
            if (-not $newCacheUntil -or $newCacheUntil -le $now) {
                $newCacheUntil = $now.AddMinutes(2)
            }
            $specCacheUntil = $newCacheUntil

            $byType = $specMeasurements | Group-Object type | ForEach-Object { "$($_.Count) $($_.Name)" }
            Write-NknLog "INFO" "Spec hämtad: $($specMeasurements.Count) mål ($($byType -join ', ')), $($canaryTargets.Count) canaries, giltig till $($specCacheUntil.ToString('HH:mm:ss'))"
        } catch {
            Write-NknLog "WARN" "Spec-hämtning misslyckades: $_"
        }
    }

    if ($now -ge $nextHeartbeatAt) {
        try {
            $hbResp = Send-Heartbeat -Config $config -CanaryTargets $canaryTargets
            if ($hbResp -and $hbResp.PSObject.Properties["next_heartbeat_in_seconds"]) {
                $heartbeatIntervalSeconds = [int]$hbResp.next_heartbeat_in_seconds
            }
        } catch {
            Write-NknLog "WARN" "Heartbeat misslyckades: $_"
        }
        $nextHeartbeatAt = $now.AddSeconds($heartbeatIntervalSeconds)
    }

    # Per-mått-intervall: kör bara mått som passerat sin interval_seconds.
    $results = @()
    foreach ($m in $specMeasurements) {
        $mid = $m.id
        $interval = [int]$m.interval_seconds
        if ($interval -le 0) { $interval = 60 }
        $due = $true
        if ($lastRun.ContainsKey($mid)) {
            $due = $now -ge $lastRun[$mid].AddSeconds($interval)
        }
        if (-not $due) { continue }
        $r = Invoke-Measurement -Measurement $m
        if ($r) { $results += $r }
        $lastRun[$mid] = $now
    }

    if ($results.Count -gt 0) {
        try {
            Send-Results -Config $config -Results $results
        } catch {
            Write-NknLog "WARN" "Inrapportering misslyckades: $_"
        }
    }

    if ($Once) { break }
    Start-Sleep -Seconds $tickSeconds
}
