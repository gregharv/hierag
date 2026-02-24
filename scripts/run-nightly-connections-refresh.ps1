param(
    [string]$ProjectRoot = "",
    [string]$LogDir = "",
    [string]$PythonExe = "",
    [int]$SiteId = 2,
    [int]$PruneMissingAfter = 1,
    [string]$PruneStatusCodes = "404,410",
    [string]$WeeklyCrawlDay = "",
    [int]$MaxPages = 3000
)

$ErrorActionPreference = "Stop"

function Parse-RefreshSummary {
    param([object[]]$Lines)

    $summary = @{}
    $inSummary = $false
    foreach ($entry in ($Lines | ForEach-Object { [string]$_ })) {
        $line = $entry.TrimEnd()
        if (-not $inSummary) {
            if ($line -eq "Summary:") {
                $inSummary = $true
            }
            continue
        }

        if ($line -match '^\s*-\s*([^:]+):\s*(.*)$') {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim()
            $summary[$key] = $value
            continue
        }

        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        if ($line -eq "Failed URLs:" -or $line -like "Traceback*") {
            break
        }

        if (-not $line.StartsWith("-")) {
            break
        }
    }

    return $summary
}

function Get-SummaryNumber {
    param(
        [hashtable]$Summary,
        [string]$Key,
        [double]$DefaultValue
    )

    if ($Summary.ContainsKey($Key)) {
        $parsed = 0.0
        if ([double]::TryParse([string]$Summary[$Key], [ref]$parsed)) {
            return $parsed
        }
    }
    return $DefaultValue
}

function Resolve-PythonExecutable {
    param([string]$Requested)

    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        return $Requested
    }

    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path $condaPython) {
            return $condaPython
        }
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        return "$($pyCmd.Source) -3"
    }

    return ""
}

function Write-RefreshLog {
    param(
        [string]$Message,
        [string]$LogPath
    )

    Write-Host $Message
    Add-Content -Path $LogPath -Value $Message
}

function Invoke-DbSnapshots {
    param(
        [string]$ProjectRoot,
        [string]$SnapshotDir,
        [string]$Stamp,
        [string]$LogPath,
        [int]$RetentionDays = 14
    )

    New-Item -ItemType Directory -Force -Path $SnapshotDir | Out-Null
    $dataDir = Join-Path $ProjectRoot "data"
    $dbFiles = @("app_runtime.db", "scraper.db")

    foreach ($dbFile in $dbFiles) {
        $source = Join-Path $dataDir $dbFile
        if (-not (Test-Path $source)) {
            Write-RefreshLog -Message ("Snapshot warning: source DB not found: {0}" -f $source) -LogPath $LogPath
            continue
        }

        $baseName = [System.IO.Path]::GetFileNameWithoutExtension($dbFile)
        $destination = Join-Path $SnapshotDir ("{0}-{1}.db" -f $baseName, $Stamp)
        try {
            Copy-Item -Path $source -Destination $destination -Force -ErrorAction Stop
            Write-RefreshLog -Message ("Snapshot created: {0}" -f $destination) -LogPath $LogPath
        } catch {
            $err = $_.Exception.Message
            Write-RefreshLog -Message ("Snapshot warning: failed to copy {0} -> {1} ({2})" -f $source, $destination, $err) -LogPath $LogPath
        }
    }

    try {
        $cutoff = (Get-Date).AddDays(-[Math]::Abs($RetentionDays))
        $removed = 0
        $oldSnapshots = Get-ChildItem -Path $SnapshotDir -Filter "*.db" -File -ErrorAction SilentlyContinue | Where-Object {
            $_.LastWriteTime -lt $cutoff
        }
        foreach ($snapshot in $oldSnapshots) {
            try {
                Remove-Item -Path $snapshot.FullName -Force -ErrorAction Stop
                $removed += 1
            } catch {
                $err = $_.Exception.Message
                Write-RefreshLog -Message ("Snapshot retention warning: failed to remove {0} ({1})" -f $snapshot.FullName, $err) -LogPath $LogPath
            }
        }
        Write-RefreshLog -Message ("Snapshot retention cleanup: removed={0} cutoff_days={1}" -f $removed, [Math]::Abs($RetentionDays)) -LogPath $LogPath
    } catch {
        $err = $_.Exception.Message
        Write-RefreshLog -Message ("Snapshot retention warning: {0}" -f $err) -LogPath $LogPath
    }
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
Set-Location $ProjectRoot

if ([string]::IsNullOrWhiteSpace($LogDir)) {
    $LogDir = Join-Path $ProjectRoot "data\logs"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $LogDir "connections-refresh-$stamp.log"
$historyPath = Join-Path $LogDir "connections-refresh-history.csv"
$snapshotDir = Join-Path $ProjectRoot "data\snapshots\nightly"
$today = (Get-Date).DayOfWeek.ToString()
$doCrawl = $true
if (-not [string]::IsNullOrWhiteSpace($WeeklyCrawlDay)) {
    $doCrawl = $today -eq $WeeklyCrawlDay
}

$pythonCommand = Resolve-PythonExecutable -Requested $PythonExe
if ([string]::IsNullOrWhiteSpace($pythonCommand)) {
    $missingPython = "No Python executable found. Set -PythonExe or install Python."
    Write-Host $missingPython
    Add-Content -Path $logPath -Value $missingPython
    exit 2
}

$pythonParts = @($pythonCommand -split '\s+')
$pythonExeResolved = $pythonParts[0]
$pythonPrefixArgs = @()
if ($pythonParts.Count -gt 1) {
    $pythonPrefixArgs = $pythonParts[1..($pythonParts.Count - 1)]
}

$depCheckArgs = @()
if ($pythonPrefixArgs.Count -gt 0) {
    $depCheckArgs += $pythonPrefixArgs
}
$depCheckArgs += @(
    "-c",
    "import httpx, fastlite; print('deps_ok')"
)
$depOutput = & $pythonExeResolved @depCheckArgs 2>&1
if ($LASTEXITCODE -ne 0) {
    $depMessage = "Python dependencies missing for nightly refresh. Install deps in this interpreter and retry."
    Write-Host $depMessage
    Write-Host $depOutput
    Add-Content -Path $logPath -Value $depMessage
    Add-Content -Path $logPath -Value ($depOutput | ForEach-Object { [string]$_ })
    exit 2
}

$refreshArgs = @()
if ($pythonPrefixArgs.Count -gt 0) {
    $refreshArgs += $pythonPrefixArgs
}
$refreshArgs += @(
    "-m",
    "core.daily_connections_refresh",
    "--site-id",
    "$SiteId",
    "--max-pages",
    "$MaxPages",
    "--prune-missing",
    "--prune-missing-after",
    "$PruneMissingAfter",
    "--prune-status-codes",
    "$PruneStatusCodes"
)

if (-not $doCrawl) {
    $refreshArgs += "--skip-crawl"
}

$startLine = "[{0}] Starting connections refresh (crawl={1}, day={2})" -f (Get-Date -Format s), $doCrawl, $today
$commandLine = "$pythonExeResolved " + ($refreshArgs -join " ")
Write-RefreshLog -Message $startLine -LogPath $logPath
Write-RefreshLog -Message $commandLine -LogPath $logPath

Invoke-DbSnapshots -ProjectRoot $ProjectRoot -SnapshotDir $snapshotDir -Stamp $stamp -LogPath $logPath -RetentionDays 14

$runStart = Get-Date
$previousErrorActionPreference = $ErrorActionPreference
$hasNativeErrorPreference = $false
$previousNativeErrorPreference = $null
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $hasNativeErrorPreference = $true
    $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
}
$ErrorActionPreference = "Continue"
try {
    $runOutput = & $pythonExeResolved @refreshArgs 2>&1 | Tee-Object -FilePath $logPath -Append
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
    if ($hasNativeErrorPreference) {
        $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
    }
}
$exitCode = $LASTEXITCODE
$elapsedSeconds = [Math]::Round(((Get-Date) - $runStart).TotalSeconds, 2)

$summary = Parse-RefreshSummary -Lines $runOutput
$pagesAdded = [int](Get-SummaryNumber -Summary $summary -Key "pages_added" -DefaultValue (Get-SummaryNumber -Summary $summary -Key "urls_new" -DefaultValue 0))
$pagesModified = [int](Get-SummaryNumber -Summary $summary -Key "pages_modified" -DefaultValue (Get-SummaryNumber -Summary $summary -Key "urls_changed" -DefaultValue 0))
$pagesRemoved = [int](Get-SummaryNumber -Summary $summary -Key "pages_removed" -DefaultValue (Get-SummaryNumber -Summary $summary -Key "pruned_pages" -DefaultValue 0))
$urlsFailed = [int](Get-SummaryNumber -Summary $summary -Key "urls_failed" -DefaultValue 0)
$targetUrls = [int](Get-SummaryNumber -Summary $summary -Key "target_urls" -DefaultValue 0)
$durationSeconds = [Math]::Round((Get-SummaryNumber -Summary $summary -Key "duration_seconds" -DefaultValue $elapsedSeconds), 2)

$metricsLine = "[{0}] Metrics: added={1} modified={2} removed={3} failed={4} target_urls={5} duration_s={6}" -f (
    (Get-Date -Format s),
    $pagesAdded,
    $pagesModified,
    $pagesRemoved,
    $urlsFailed,
    $targetUrls,
    $durationSeconds
)
Write-Host $metricsLine
Add-Content -Path $logPath -Value $metricsLine

$record = [PSCustomObject]@{
    timestamp_utc    = (Get-Date).ToUniversalTime().ToString("o")
    day              = $today
    crawl_enabled    = [bool]$doCrawl
    exit_code        = [int]$exitCode
    pages_added      = $pagesAdded
    pages_modified   = $pagesModified
    pages_removed    = $pagesRemoved
    urls_failed      = $urlsFailed
    target_urls      = $targetUrls
    duration_seconds = $durationSeconds
    log_file         = [System.IO.Path]::GetFileName($logPath)
}
if (Test-Path $historyPath) {
    $record | Export-Csv -Path $historyPath -NoTypeInformation -Append
} else {
    $record | Export-Csv -Path $historyPath -NoTypeInformation
}

$endLine = "[{0}] Completed with exit code {1}. Log: {2}" -f (Get-Date -Format s), $exitCode, $logPath
Write-Host $endLine
Add-Content -Path $logPath -Value $endLine
exit $exitCode
