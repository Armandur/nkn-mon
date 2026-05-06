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
    } | ConvertTo-Json -Depth 5

    Write-NknLog "INFO" "Registrerar mot $CoordinatorUrl med site_name='$($meta.SiteName)'"
    $resp = Invoke-RestMethod -Method Post -Uri "$CoordinatorUrl/probe/register" `
        -Body $payload -ContentType "application/json"

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

function Invoke-IcmpPing {
    param([pscustomobject]$Measurement)

    $packetCount = 4
    if ($Measurement.PSObject.Properties["extra"] -and $Measurement.extra.PSObject.Properties["packet_count"]) {
        $packetCount = [int]$Measurement.extra.packet_count
    }

    $target = $Measurement.target
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    $result = [ordered]@{
        measurement_id = $Measurement.id
        timestamp = $ts
        type = "icmp_ping"
        target = $target
        success = $false
        rtt_ms_min = $null
        rtt_ms_avg = $null
        rtt_ms_max = $null
        packet_loss_pct = 100.0
    }

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
        Write-NknLog "WARN" "Ping mot $target misslyckades: $_"
    }
    return [pscustomobject]$result
}

function Send-Results {
    param([pscustomobject]$Config, [object[]]$Results)
    if (-not $Results -or $Results.Count -eq 0) { return }
    $payload = @{
        client_id = $Config.client_id
        results = $Results
    } | ConvertTo-Json -Depth 6
    $headers = @{ Authorization = "Bearer $($Config.client_token)" }
    $resp = Invoke-RestMethod -Method Post -Uri "$CoordinatorUrl/probe/results" `
        -Headers $headers -Body $payload -ContentType "application/json"
    Write-NknLog "INFO" "Skickade $($Results.Count) resultat: accepted=$($resp.accepted) rejected=$($resp.rejected)"
}

# --- Huvudloop -------------------------------------------------------------

$config = Read-LocalConfig
if (-not $config -or -not $config.client_token -or $config.coordinator_url -ne $CoordinatorUrl) {
    $config = Register-Probe
}

$specCacheUntil = [datetime]::MinValue
$specMeasurements = @()
$heartbeatSeconds = 60

while ($true) {
    if ((Get-Date) -ge $specCacheUntil) {
        try {
            $spec = Get-Spec -Token $config.client_token
            $specMeasurements = @($spec.measurements | Where-Object { $_.type -eq "icmp_ping" })
            $specCacheUntil = (Get-Date).AddMinutes(10)
            Write-NknLog "INFO" "Spec hämtad: $($specMeasurements.Count) icmp_ping-mål"
        } catch {
            Write-NknLog "WARN" "Spec-hämtning misslyckades: $_"
        }
    }

    $results = @()
    foreach ($m in $specMeasurements) {
        $results += Invoke-IcmpPing -Measurement $m
    }

    if ($results.Count -gt 0) {
        try {
            Send-Results -Config $config -Results $results
        } catch {
            Write-NknLog "WARN" "Inrapportering misslyckades: $_"
        }
    }

    if ($Once) { break }

    # Iteration 2 v0.1: ett gemensamt intervall för alla mätningar.
    # Per-mått-intervall (specens interval_seconds) implementeras i nästa version.
    Start-Sleep -Seconds $heartbeatSeconds
}
