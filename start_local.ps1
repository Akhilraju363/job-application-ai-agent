<#
.SYNOPSIS
    Start the Job Application AI Agent locally: the LOCAL_MODE pipeline and the dashboard.

.DESCRIPTION
    Orchestration only -- it runs the same two commands you would type by hand:

        $env:LOCAL_MODE = "true"
        python scripts/run_pipeline.py
        python scripts/dashboard_server.py

    as two separate processes, so the dashboard stays available while the pipeline runs.
    Both share this console, so their output (and any traceback) appears here.

    The pipeline is a one-shot run: when it finishes (or fails) that is reported and the
    dashboard keeps running. If the dashboard exits, the script reports it, stops the
    pipeline and exits. Ctrl+C stops both. Only the process trees this script started are
    stopped -- never other Python processes.

.PARAMETER Python
    Python interpreter to use. Default: the active virtual environment, else the repo's
    .venv, else `python` on PATH. No environment is ever created.

.PARAMETER Port
    Dashboard port (default 8765, same as scripts/dashboard_server.py).

.PARAMETER NoPipeline
    Start only the dashboard.

.PARAMETER DryRun
    Print the resolved interpreter, repository root and commands, then exit without starting anything.

.EXAMPLE
    .\start_local.ps1        # from the repository root
#>
[CmdletBinding()]
param(
    [string]$Python,
    [int]$Port = 8765,
    [switch]$NoPipeline,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot   # this script lives in the repository root; the current directory never matters

function Fail([string]$message) {
    Write-Host "ERROR: $message" -ForegroundColor Red
    exit 1
}

function Resolve-Python {
    if ($Python) { return $Python }
    $candidates = @()
    if ($env:VIRTUAL_ENV) { $candidates += Join-Path $env:VIRTUAL_ENV "Scripts\python.exe" }
    $candidates += Join-Path $RepoRoot ".venv\Scripts\python.exe"
    foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$py = Resolve-Python
if (-not $py) {
    Fail "Python was not found. Activate your virtual environment, create .venv in the repository, or pass -Python <path>."
}
try {
    $version = (& $py --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
} catch {
    Fail "Python at '$py' could not be run ($_)."
}

$pipelineScript = Join-Path $RepoRoot "scripts\run_pipeline.py"
$dashboardScript = Join-Path $RepoRoot "scripts\dashboard_server.py"
foreach ($s in @($pipelineScript, $dashboardScript)) {
    if (-not (Test-Path -LiteralPath $s)) { Fail "Missing $s" }
}
$pipelineArgs = @("`"$pipelineScript`"")
$dashboardArgs = @("`"$dashboardScript`"", "--port", "$Port")

if ($DryRun) {
    Write-Output "RepoRoot:  $RepoRoot"
    Write-Output "Python:    $py ($version)"
    Write-Output "LOCAL_MODE=true"
    if (-not $NoPipeline) { Write-Output "Pipeline:  $py $($pipelineArgs -join ' ')" }
    Write-Output "Dashboard: $py $($dashboardArgs -join ' ')"
    exit 0
}

# The dashboard can't start if the port is taken (often an earlier dashboard still running).
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $owner = ($busy | Select-Object -First 1).OwningProcess
    Fail ("Port $Port is already in use by process $owner. Stop that process (for an old dashboard: " +
          "Stop-Process -Id $owner) or pass -Port <other port>.")
}

function Stop-Tree([System.Diagnostics.Process]$proc, [string]$label) {
    if ($proc -and -not $proc.HasExited) {
        Write-Host "Stopping $label (PID $($proc.Id))..."
        # /T: the whole tree this script started (the venv launcher's interpreter, pipeline step
        # scripts) -- and nothing else.
        & taskkill.exe /PID $proc.Id /T /F 2>&1 | Out-Null
    }
}

$previousLocalMode = $env:LOCAL_MODE
$env:LOCAL_MODE = "true"          # inherited by both child processes
$pipeline = $null
$dashboard = $null
$exitCode = 0

try {
    Write-Host "Job Application AI Agent -- local startup"
    Write-Host "  Repository: $RepoRoot"
    Write-Host "  Python:     $py ($version)"
    Write-Host "  LOCAL_MODE: true"

    $dashboard = Start-Process -FilePath $py -ArgumentList $dashboardArgs -WorkingDirectory $RepoRoot -NoNewWindow -PassThru
    $null = $dashboard.Handle     # keep a handle so ExitCode is readable after exit
    Write-Host "  Dashboard:  PID $($dashboard.Id) -> http://127.0.0.1:$Port/"

    if (-not $NoPipeline) {
        $pipeline = Start-Process -FilePath $py -ArgumentList $pipelineArgs -WorkingDirectory $RepoRoot -NoNewWindow -PassThru
        $null = $pipeline.Handle
        Write-Host "  Pipeline:   PID $($pipeline.Id) (scripts\run_pipeline.py)"
    }
    Write-Host "Press Ctrl+C to stop."
    Write-Host ""

    $pipelineReported = $NoPipeline.IsPresent
    while ($true) {
        if (-not $pipelineReported -and $pipeline.HasExited) {
            $pipelineReported = $true
            if ($pipeline.ExitCode -eq 0) {
                Write-Host "Pipeline finished successfully (PID $($pipeline.Id)). Dashboard still running on http://127.0.0.1:$Port/ -- Ctrl+C to stop."
            } else {
                Write-Warning "Pipeline exited with code $($pipeline.ExitCode) (PID $($pipeline.Id)). See the output above and output\logs\. Dashboard still running -- Ctrl+C to stop."
            }
        }
        if ($dashboard.HasExited) {
            Write-Warning "Dashboard server exited unexpectedly with code $($dashboard.ExitCode) (PID $($dashboard.Id)). See the output above."
            $exitCode = 1
            break
        }
        Start-Sleep -Milliseconds 500
    }
}
finally {
    # Runs on normal exit and on Ctrl+C. Children in this console get Ctrl+C themselves; give
    # them a moment to shut down cleanly, then stop whatever is left of their trees.
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline -and (($pipeline -and -not $pipeline.HasExited) -or ($dashboard -and -not $dashboard.HasExited))) {
        Start-Sleep -Milliseconds 200
        if ($exitCode -ne 0) { break }   # dashboard already gone: don't wait on a pipeline that's mid-run
    }
    Stop-Tree $pipeline "pipeline"
    Stop-Tree $dashboard "dashboard server"
    $env:LOCAL_MODE = $previousLocalMode
    Write-Host "Stopped."
}
exit $exitCode
