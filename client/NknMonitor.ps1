<#
# Version: 0.6.3
.SYNOPSIS
    NKN-Monitor probe-klient (PowerShell).

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

.PARAMETER LogFile
    Skriv loggrader även till denna fil utöver konsolen. Användbart vid
    Scheduled Task-körning där stdout försvinner. Filen roteras inte
    automatiskt - använd Windows Task Scheduler eller logrotate för det.

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
    [string]$LogFile = "",
    [switch]$Once,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$CoordinatorUrl = $CoordinatorUrl.TrimEnd("/")

# UTF-8 i alla riktningar:
# - Konsolens OutputEncoding så svenska tecken visas korrekt i logg
# - $OutputEncoding så stdin till externa kommandon blir UTF-8
# - PS 5.1:s default är ofta IBM850/Windows-1252 vilket bryter åäö
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

# Warmup: ladda DnsClient-modulen i förväg så att första Resolve-DnsName i
# mätloopen inte beskattas av modulladdning (~1 sekund i kalla körningar).
Import-Module DnsClient -ErrorAction SilentlyContinue | Out-Null

# System.Security krävs för DPAPI (ProtectedData). PS 5.1 laddar inte
# den auto, .NET Framework har den inbyggd men assembly måste inkluderas.
try { Add-Type -AssemblyName "System.Security" -ErrorAction SilentlyContinue } catch {}

$BufferPath = Join-Path (Split-Path -Parent $ConfigPath) "buffer.jsonl"
$BufferRetentionDays = 7
$Script:NknClientVersion = "0.6.3"

# Cache av primär lokal IPv4. Uppdateras vid varje heartbeat.
$Global:NknPrimaryLocalIp = $null

function Write-NknLog {
    param([string]$Level, [string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts $Level $Message"
    Write-Host $line
    if ($LogFile) {
        try {
            $dir = Split-Path -Parent $LogFile
            if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
            $sw = [System.IO.StreamWriter]::new($LogFile, $true, [System.Text.UTF8Encoding]::new($false))
            try { $sw.WriteLine($line) } finally { $sw.Close() }
        } catch {}
    }
}

function Protect-Token {
    # DPAPI-skydd för bearer-token: bara samma user på samma maskin kan dekryptera.
    # Om DPAPI inte är tillgänglig (icke-Windows, äldre PS) faller vi tillbaka på
    # klartext men loggar varning.
    param([string]$Plain)
    if (-not $Plain) { return "" }
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Plain)
        $protected = [System.Security.Cryptography.ProtectedData]::Protect(
            $bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        return "dpapi:" + [Convert]::ToBase64String($protected)
    } catch {
        Write-NknLog "WARN" "DPAPI-kryptering ej tillgänglig - token sparas i klartext"
        return $Plain
    }
}

function Unprotect-Token {
    param([string]$Stored)
    if (-not $Stored) { return "" }
    if (-not $Stored.StartsWith("dpapi:")) { return $Stored }  # gammalt klartext-format
    try {
        $b64 = $Stored.Substring(6)
        $bytes = [Convert]::FromBase64String($b64)
        $plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
            $bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        return [System.Text.Encoding]::UTF8.GetString($plain)
    } catch {
        Write-NknLog "WARN" "Kunde inte dekryptera token: $_"
        return ""
    }
}

function Update-Self {
    <#
        Ladda ner ny klientversion från coordinator, verifiera SHA-256
        och ersätt aktuella skriptet på disk. Backup till .old så
        man kan rollback:a manuellt vid behov. Avslutar processen efter
        lyckad uppdatering - Scheduled Task eller manuell restart plockar
        upp den nya versionen.
    #>
    param([string]$Url, [string]$ExpectedSha256, [string]$ExpectedVersion, [string]$Token)

    $current = $PSCommandPath
    if (-not $current -or -not (Test-Path $current)) {
        Write-NknLog "WARN" "Vet inte vart skriptet bor på disk - update skippas"
        return
    }

    $tmp = "$current.new"
    try {
        Write-NknLog "INFO" "Laddar ner ny klient v$ExpectedVersion från $Url"
        $headers = @{ Authorization = "Bearer $Token" }
        Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing -TimeoutSec 60 -Headers $headers
        $actualHash = (Get-FileHash -Path $tmp -Algorithm SHA256).Hash.ToLower()
        $expected = $ExpectedSha256.ToLower()
        if ($actualHash -ne $expected) {
            Write-NknLog "ERROR" "SHA256-mismatch ($actualHash vs $expected) - rör inte filen"
            Remove-Item $tmp -ErrorAction SilentlyContinue
            return
        }
        $backup = "$current.old"
        Remove-Item $backup -ErrorAction SilentlyContinue
        Move-Item -Path $current -Destination $backup -Force
        Move-Item -Path $tmp -Destination $current -Force
        Write-NknLog "INFO" "Skript uppdaterat till v$ExpectedVersion. Avslutar för restart."
        # Exit normalt så Scheduled Task (vid Restart on failure) eller manuell
        # nystart plockar upp den nya filen. PS-script kan inte enkelt re-exec
        # sig själv mid-pipeline.
        exit 0
    } catch {
        Write-NknLog "ERROR" "Update misslyckades: $_"
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

function Test-ClockSync {
    # Klient-klocka som ligger >5 min fel mot coordinator gör att resultat
    # avvisas (timestamp utanför 7-dagarsfönstret blir extremt sällan men
    # är ofta första symtomet på en glömd tidssync). Heartbeat-svaret
    # innehåller inte serverklocka, så vi kollar HTTP Date-headern istället.
    param([string]$BaseUrl)
    try {
        $resp = Invoke-WebRequest -Uri "$BaseUrl/healthz" -UseBasicParsing -TimeoutSec 5
        $dateHdr = $resp.Headers["Date"]
        if (-not $dateHdr) { return }
        $serverTime = [datetime]::Parse($dateHdr).ToUniversalTime()
        $localTime = (Get-Date).ToUniversalTime()
        $diff = [Math]::Abs(($localTime - $serverTime).TotalSeconds)
        if ($diff -gt 60) {
            Write-NknLog "WARN" "Klockskillnad mot server: ${diff}s. Synka med w32tm /resync om felet är permanent."
        }
    } catch {}
}

function Read-LocalConfig {
    if (Test-Path $ConfigPath) {
        try {
            $cfg = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
            # Migrera gammal klartext-token: kryptera vid nästa write
            if ($cfg.PSObject.Properties["client_token"] -and $cfg.client_token) {
                $cfg | Add-Member -NotePropertyName "_plain_token" -NotePropertyValue (Unprotect-Token -Stored $cfg.client_token) -Force
            }
            return $cfg
        } catch {
            Write-NknLog "WARN" "Kunde inte läsa $ConfigPath - registrerar om: $_"
        }
    }
    return $null
}

function Get-PlainToken {
    param([pscustomobject]$Config)
    if ($Config.PSObject.Properties["_plain_token"] -and $Config._plain_token) {
        return $Config._plain_token
    }
    return Unprotect-Token -Stored $Config.client_token
}

function Write-Utf8NoBom {
    # PS 5.1:s 'Set-Content -Encoding UTF8' skriver med BOM, vilket bryter
    # JSONL-parsing av första raden. .NET med UTF8Encoding(false) ger ren UTF-8.
    param([string]$Path, [string]$Content)
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function Save-LocalConfig {
    param([pscustomobject]$Config)
    $dir = Split-Path -Parent $ConfigPath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    Write-Utf8NoBom -Path $ConfigPath -Content ($Config | ConvertTo-Json -Depth 10)
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

function Test-IsAuthError {
    param($ErrorRecord)
    try {
        $resp = $ErrorRecord.Exception.Response
        if ($resp -and [int]$resp.StatusCode -eq 401) { return $true }
    } catch {}
    return $false
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

    $protectedToken = Protect-Token -Plain $resp.client_token
    $cfg = [pscustomobject]@{
        coordinator_url = $CoordinatorUrl
        client_id = $resp.client_id
        client_token = $protectedToken
        site_name = $meta.SiteName
        registered_at = (Get-Date).ToString("o")
    }
    Save-LocalConfig -Config $cfg
    # Cacha plaintext i minnet under körningen så vi inte dekrypterar varje request
    $cfg | Add-Member -NotePropertyName "_plain_token" -NotePropertyValue $resp.client_token -Force
    Write-NknLog "INFO" "Registrerad: client_id=$($resp.client_id)"
    return $cfg
}

function Get-Spec {
    param([string]$Token)
    $headers = @{ Authorization = "Bearer $Token" }
    return Invoke-RestMethod -Method Get -Uri "$CoordinatorUrl/probe/spec" -Headers $headers -UseBasicParsing
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
    $category = "builtin"
    if ($Measurement.PSObject.Properties["category"] -and $Measurement.category) {
        $category = [string]$Measurement.category
    }
    $peerSite = $null
    if ($Measurement.PSObject.Properties["extra"] -and $Measurement.extra -and `
        $Measurement.extra.PSObject.Properties["peer_site"]) {
        $peerSite = [string]$Measurement.extra.peer_site
    }
    $senderIp = $null
    if ($category -eq "peer") {
        $senderIp = $Global:NknPrimaryLocalIp
    }
    return [ordered]@{
        measurement_id = $Measurement.id
        timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        type = $Type
        target = $Measurement.target
        success = $false
        category = $category
        peer_site = $peerSite
        sender_local_ip = $senderIp
        traceroute_path_hosts = $null
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

function Resolve-HopHostname {
    param([string]$Ip)
    if ([string]::IsNullOrWhiteSpace($Ip)) { return $null }
    try {
        $entry = [System.Net.Dns]::GetHostEntry($Ip)
        if ($entry.HostName -and $entry.HostName -ne $Ip) {
            return $entry.HostName
        }
    } catch {}
    return $null
}

function Invoke-Traceroute {
    param([pscustomobject]$Measurement)

    $maxHops = [int](Get-MeasurementExtra -Measurement $Measurement -Key "max_hops" -Default 30)
    $target = $Measurement.target
    $result = New-NknResult -Measurement $Measurement -Type "traceroute"
    $result.traceroute_hops = $null
    $result.traceroute_total_ms = $null
    $result.traceroute_path = @()
    $result.traceroute_path_hosts = @()

    try {
        $tnc = Test-NetConnection -ComputerName $target -TraceRoute -Hops $maxHops `
            -InformationLevel Detailed -ErrorAction Stop -WarningAction SilentlyContinue
        if ($tnc.PingSucceeded) {
            $result.success = $true
            if ($tnc.PingReplyDetails) {
                $result.traceroute_total_ms = [double]$tnc.PingReplyDetails.RoundtripTime
            }
        }
        if ($tnc.TraceRoute) {
            $hops = @($tnc.TraceRoute | Where-Object { $_ })
            $result.traceroute_path = $hops
            $result.traceroute_hops = $hops.Count
            # Reverse DNS från klientens nät - fångar interna NKN-namn
            # som inte finns i publik DNS
            $result.traceroute_path_hosts = @($hops | ForEach-Object { Resolve-HopHostname -Ip $_ })
        }
    } catch {
        Write-NknLog "WARN" "traceroute mot $target misslyckades: $_"
    }
    return [pscustomobject]$result
}

function Invoke-Measurement {
    param([pscustomobject]$Measurement)
    switch ($Measurement.type) {
        "icmp_ping"  { return Invoke-IcmpPing  -Measurement $Measurement }
        "tcp_ping"   { return Invoke-TcpPing   -Measurement $Measurement }
        "dns_query"  { return Invoke-DnsQuery  -Measurement $Measurement }
        "http_get"   { return Invoke-HttpGet   -Measurement $Measurement }
        "traceroute" { return Invoke-Traceroute -Measurement $Measurement }
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
    $headers = @{ Authorization = "Bearer $(Get-PlainToken -Config $Config)" }
    $resp = Invoke-NknJson -Method Post -Uri "$CoordinatorUrl/probe/results" -Headers $headers -Body $payload
    Write-NknLog "INFO" "Skickade $($Results.Count) resultat: accepted=$($resp.accepted) rejected=$($resp.rejected)"
}

function Save-ResultsToBuffer {
    param([object[]]$Results, [string]$Path)
    if (-not $Results -or $Results.Count -eq 0) { return }
    try {
        $dir = Split-Path -Parent $Path
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        $lines = $Results | ForEach-Object { $_ | ConvertTo-Json -Depth 6 -Compress }
        # Append utan BOM via .NET StreamWriter
        $sw = [System.IO.StreamWriter]::new($Path, $true, [System.Text.UTF8Encoding]::new($false))
        try {
            foreach ($l in $lines) { $sw.WriteLine($l) }
        } finally {
            $sw.Close()
        }
        $size = (Get-Item $Path).Length
        Write-NknLog "WARN" "Buffrade $($Results.Count) resultat -> $Path ($size bytes totalt)"
    } catch {
        Write-NknLog "ERROR" "Kunde INTE skriva buffer ($Path): $_"
    }
}

function Invoke-BufferFlush {
    param([pscustomobject]$Config, [string]$Path, [int]$RetentionDays = 7)
    if (-not (Test-Path $Path)) { return }

    $lines = @(Get-Content -Path $Path -Encoding UTF8 -ErrorAction SilentlyContinue)
    if ($lines.Count -eq 0) {
        Remove-Item $Path -ErrorAction SilentlyContinue
        return
    }

    $cutoff = (Get-Date).ToUniversalTime().AddDays(-$RetentionDays)
    $buffered = @()
    $expired = 0
    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $obj = $line | ConvertFrom-Json
            $ts = [DateTime]::Parse($obj.timestamp).ToUniversalTime()
            if ($ts -lt $cutoff) { $expired++; continue }
            $buffered += $obj
        } catch {
            # Korrupt rad - hoppa över
        }
    }

    if ($buffered.Count -eq 0) {
        Remove-Item $Path -ErrorAction SilentlyContinue
        if ($expired -gt 0) { Write-NknLog "INFO" "Buffer rensad: $expired resultat var äldre än $RetentionDays dagar" }
        return
    }

    Write-NknLog "INFO" "Försöker flusha $($buffered.Count) buffrade resultat..."
    $batchSize = 200
    $sent = 0
    $remaining = @($buffered)
    while ($remaining.Count -gt 0) {
        $batch = @($remaining[0..([Math]::Min($batchSize - 1, $remaining.Count - 1))])
        try {
            Send-Results -Config $Config -Results $batch
            $sent += $batch.Count
            if ($remaining.Count -le $batchSize) {
                $remaining = @()
            } else {
                $remaining = @($remaining[$batchSize..($remaining.Count - 1)])
            }
        } catch {
            Write-NknLog "WARN" "Flush avbruten efter $sent skickade: $_"
            break
        }
    }

    if ($remaining.Count -eq 0) {
        Remove-Item $Path -ErrorAction SilentlyContinue
        Write-NknLog "INFO" "Buffer flushad: $sent resultat skickade, $expired övergamla rensade"
    } else {
        # Atomic rewrite: skriv till .tmp och rename över originalet så vi
        # aldrig lämnar en halv buffer-fil vid kraschar mid-write.
        $tmp = "$Path.tmp"
        $content = ($remaining | ForEach-Object { $_ | ConvertTo-Json -Depth 6 -Compress }) -join "`n"
        Write-Utf8NoBom -Path $tmp -Content ($content + "`n")
        Move-Item -Path $tmp -Destination $Path -Force
        Write-NknLog "INFO" "Buffer delvis flushad: $sent skickade, $($remaining.Count) kvar"
    }
}

function Get-BufferSize {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return 0 }
    $lines = @(Get-Content -Path $Path -Encoding UTF8 -ErrorAction SilentlyContinue)
    return $lines.Count
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

    # Exkludera virtuella interface (Docker, VPN, Hyper-V, loopback) som inte
    # representerar siten - annars hamnar peer-tilldelningen på fel subnet.
    $excludeAlias = '^(Loopback|Tailscale|WireGuard|OpenVPN|vEthernet|Bluetooth|Docker|Hyper-?V|TAP-Windows|VirtualBox|VMware)'
    $localIPv4 = @()
    try {
        $localIPv4 = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -ne "127.0.0.1" -and
                $_.PrefixOrigin -ne "WellKnown" -and
                $_.IPAddress -notlike "169.254.*" -and
                $_.InterfaceAlias -notmatch $excludeAlias
            } |
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

    # Cacha primär lokal IPv4 så peer-resultat kan tagga "från-IP"
    if ($netCtx.local_ipv4 -and $netCtx.local_ipv4.Count -gt 0) {
        $Global:NknPrimaryLocalIp = [string]$netCtx.local_ipv4[0]
    }

    $payload = @{
        timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        version = $Script:NknClientVersion
        network_context = $netCtx
        host_info = $hostInfo
    }
    $headers = @{ Authorization = "Bearer $(Get-PlainToken -Config $Config)" }
    $resp = Invoke-NknJson -Method Post -Uri "$CoordinatorUrl/probe/heartbeat" -Headers $headers -Body $payload
    Write-NknLog "INFO" "Heartbeat: classification=$($resp.network_classification), public_ip=$($netCtx.public_ip), canaries=$($netCtx.canary_results.Count), nästa om $($resp.next_heartbeat_in_seconds)s"
    return $resp
}

# --- Huvudloop -------------------------------------------------------------

$psVer = "$($PSVersionTable.PSVersion.Major).$($PSVersionTable.PSVersion.Minor)"
Write-NknLog "INFO" "NKN-Monitor klient v$Script:NknClientVersion startad på $env:COMPUTERNAME (PowerShell $psVer, PID $PID)"

$config = Read-LocalConfig
if (-not $config -or -not $config.client_token -or $config.coordinator_url -ne $CoordinatorUrl) {
    $config = Register-Probe
} elseif ($config.client_token -and -not $config.client_token.StartsWith("dpapi:")) {
    # Gammal klartext-token - kryptera om och spara
    Write-NknLog "INFO" "Migrerar token till DPAPI-skydd"
    $config.client_token = Protect-Token -Plain $config.client_token
    Save-LocalConfig -Config $config
}

# Klock-sync-check vid uppstart
Test-ClockSync -BaseUrl $CoordinatorUrl

Write-NknLog "INFO" "Buffer-fil: $BufferPath"
$initialBuffered = Get-BufferSize -Path $BufferPath
if ($initialBuffered -gt 0) {
    Write-NknLog "INFO" "Lokal buffer innehåller $initialBuffered resultat - försöker flusha vid uppstart"
    try { Invoke-BufferFlush -Config $config -Path $BufferPath -RetentionDays $BufferRetentionDays } catch { Write-NknLog "WARN" "Initial flush misslyckades: $_" }
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
            $spec = Get-Spec -Token (Get-PlainToken -Config $config)
            $supportedTypes = @("icmp_ping", "tcp_ping", "dns_query", "http_get", "traceroute")
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
            if (Test-IsAuthError $_) {
                Write-NknLog "WARN" "Spec-fetch fick 401 - registrerar om"
                $config = Register-Probe
                $specCacheUntil = [datetime]::MinValue
                $nextHeartbeatAt = [datetime]::MinValue
                $lastRun = @{}
                continue
            }
            Write-NknLog "WARN" "Spec-hämtning misslyckades: $_"
        }
    }

    if ($now -ge $nextHeartbeatAt) {
        try {
            $hbResp = Send-Heartbeat -Config $config -CanaryTargets $canaryTargets
            if ($hbResp -and $hbResp.PSObject.Properties["next_heartbeat_in_seconds"]) {
                $heartbeatIntervalSeconds = [int]$hbResp.next_heartbeat_in_seconds
            }
            $nextHeartbeatAt = $now.AddSeconds($heartbeatIntervalSeconds)

            # Erbjuden klientuppdatering?
            if ($hbResp -and $hbResp.PSObject.Properties["client_update"] -and $hbResp.client_update) {
                $cu = $hbResp.client_update
                if ($cu.version -and $cu.version -ne $Script:NknClientVersion) {
                    Write-NknLog "INFO" "Servern erbjuder klient v$($cu.version) (har $($Script:NknClientVersion))"
                    Update-Self -Url "$CoordinatorUrl$($cu.url)" `
                        -ExpectedSha256 $cu.sha256 -ExpectedVersion $cu.version `
                        -Token (Get-PlainToken -Config $config)
                }
            }
        } catch {
            if (Test-IsAuthError $_) {
                Write-NknLog "WARN" "Heartbeat fick 401 - registrerar om"
                $config = Register-Probe
                $specCacheUntil = [datetime]::MinValue
                $nextHeartbeatAt = [datetime]::MinValue
                $lastRun = @{}
                continue
            }
            Write-NknLog "WARN" "Heartbeat misslyckades: $_"
            $nextHeartbeatAt = $now.AddSeconds($heartbeatIntervalSeconds)
        }
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
            # Lyckad skickning - passa på att tömma buffer om den finns
            Invoke-BufferFlush -Config $config -Path $BufferPath -RetentionDays $BufferRetentionDays
        } catch {
            $exType = if ($_.Exception) { $_.Exception.GetType().Name } else { "okänt" }
            if (Test-IsAuthError $_) {
                Write-NknLog "WARN" "Results-rapport fick 401 - registrerar om"
                $config = Register-Probe
                $specCacheUntil = [datetime]::MinValue
                $nextHeartbeatAt = [datetime]::MinValue
                $lastRun = @{}
            } else {
                Write-NknLog "WARN" "Inrapportering misslyckades ($exType), buffrar lokalt: $($_.Exception.Message)"
                Save-ResultsToBuffer -Results $results -Path $BufferPath
            }
        }
    }

    if ($Once) { break }
    Start-Sleep -Seconds $tickSeconds
}
