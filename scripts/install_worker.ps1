<#
.SYNOPSIS
  Set up a Quran Studio render worker on this machine, in one command.

.DESCRIPTION
  Clones (or updates) the repo, creates a virtualenv, installs the worker,
  writes worker/.env, registers a hidden auto-start task, and starts polling.

  By default it installs the LIGHT worker: downloads, overlay renders, search,
  and YouTube upload. That is a ~300 MB install. Pass -WithMusicRemoval to add
  Demucs and PyTorch (~5 GB) — only worth it on a machine with an NVIDIA GPU,
  since Demucs on CPU runs 10-20x slower than real time.

.EXAMPLE
  irm https://raw.githubusercontent.com/Affiliat0r/yt-audio-filter/main/scripts/install_worker.ps1 | iex

.EXAMPLE
  .\install_worker.ps1 -StudioUrl https://quran-studio-mocha.vercel.app -WorkerToken xxx -WithMusicRemoval
#>
[CmdletBinding()]
param(
  [string]$StudioUrl,
  [string]$WorkerToken,
  [string]$BlobToken,
  [string]$BypassSecret,
  [string]$InstallDir = "$HOME\yt-audio-filter",
  [switch]$WithMusicRemoval,
  [switch]$NoAutoStart
)

$ErrorActionPreference = 'Stop'
function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  $m" -ForegroundColor Yellow }

<#
Run a native command without letting PowerShell 5.1 mistake its chatter for
failure.

With $ErrorActionPreference='Stop', 5.1 wraps anything an external program
writes to stderr in a NativeCommandError and treats it as terminating. pip
writes ordinary warnings there ("A new release of pip is available", build
notes), so a completely successful install aborted the script — the first
version of this installer died right after creating the venv, leaving no
worker/.env and no worker, with nothing on screen to say why.

Exit code is the only trustworthy signal, so check that instead.
#>
function Invoke-Native {
  param([Parameter(Mandatory)][string]$Exe, [string[]]$Arguments, [string]$What)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $Exe @Arguments
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $prev
  }
  if ($code -ne 0) { throw "$What failed (exit $code). Command: $Exe $($Arguments -join ' ')" }
}

Write-Host "`nQuran Studio worker setup`n" -ForegroundColor White

# --- prerequisites ----------------------------------------------------------
# Checked up front: failing here after a multi-GB download would be rude.
foreach ($tool in 'git', 'python') {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    throw "$tool is not on PATH. Install it and re-run. (winget install Git.Git / Python.Python.3.12)"
  }
}
$pyVer = (python -c "import sys;print('.'.join(map(str,sys.version_info[:2])))").Trim()
if ([version]$pyVer -lt [version]'3.10') { throw "Python $pyVer found; 3.10+ is required." }
Ok "git and Python $pyVer present"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Warn "ffmpeg is NOT on PATH. Renders will fail until you install it:"
  Warn "    winget install Gyan.FFmpeg     (then reopen the terminal)"
} else {
  Ok "ffmpeg present"
}

# --- repo -------------------------------------------------------------------
if (Test-Path (Join-Path $InstallDir '.git')) {
  Info "Updating existing checkout at $InstallDir"
  # git writes progress to stderr, so this needs the same treatment as pip.
  Invoke-Native git @('-C', $InstallDir, 'pull', '--ff-only') 'git pull'
} else {
  Info "Cloning into $InstallDir"
  Invoke-Native git @('clone', '--depth', '1', 'https://github.com/Affiliat0r/yt-audio-filter.git', $InstallDir) 'git clone'
}
Set-Location $InstallDir

# --- venv + deps ------------------------------------------------------------
$venvPy = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  Info "Creating virtualenv"
  Invoke-Native python @('-m', 'venv', '.venv') 'venv creation'
}

Invoke-Native $venvPy @('-m','pip','install','--upgrade','pip','--quiet','--disable-pip-version-check') 'pip upgrade'
if ($WithMusicRemoval) {
  Info "Installing worker WITH Demucs + PyTorch (~5 GB, this takes a while)"
  Invoke-Native $venvPy @('-m','pip','install','-e','.[music]','--disable-pip-version-check') 'pip install'
} else {
  Info "Installing light worker (~300 MB): downloads, overlay renders, search, upload"
  Invoke-Native $venvPy @('-m','pip','install','-e','.','--disable-pip-version-check') 'pip install'
}

# Prove it actually works before claiming success. A half-installed venv used
# to sail past this point and only surface as a silent worker much later.
Invoke-Native $venvPy @('-c','import yt_audio_filter, worker.worker') 'import check'
Ok "Dependencies installed and import-checked"

# --- config -----------------------------------------------------------------
if (-not $StudioUrl)   { $StudioUrl = Read-Host "Studio URL (e.g. https://quran-studio-mocha.vercel.app)" }
if (-not $WorkerToken) { $WorkerToken = Read-Host "WORKER_TOKEN (same value as on Vercel)" }

$envPath = Join-Path $InstallDir 'worker\.env'
$lines = @(
  "STUDIO_BASE_URL=$($StudioUrl.TrimEnd('/'))",
  "WORKER_TOKEN=$WorkerToken",
  "WORKER_POLL_SECONDS=5",
  "WORKER_CACHE_DIR=cache"
)
if ($BlobToken)    { $lines += "BLOB_READ_WRITE_TOKEN=$BlobToken" }
if ($BypassSecret) { $lines += "VERCEL_AUTOMATION_BYPASS_SECRET=$BypassSecret" }
# UTF8 without BOM: the .env parser reads plain text and a BOM would end up
# inside the first key's name.
[IO.File]::WriteAllLines($envPath, $lines, (New-Object Text.UTF8Encoding $false))
Ok "Wrote worker\.env"

# --- auto-start -------------------------------------------------------------
if (-not $NoAutoStart) {
  $vbs = Join-Path $InstallDir 'worker\run_worker_hidden.vbs'
  $action  = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "//B //Nologo `"$vbs`"" -WorkingDirectory $InstallDir
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
      -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
      -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName 'QuranStudioWorker' -Action $action -Trigger $trigger `
      -Settings $settings -Description 'Polls Quran Studio for render jobs.' -Force | Out-Null
  Start-ScheduledTask -TaskName 'QuranStudioWorker'
  Ok "Registered auto-start task 'QuranStudioWorker' and started it"
  Info "Logs: $InstallDir\worker\worker.log"
} else {
  Info "Skipped auto-start. Run it with: worker\run_worker.bat"
}

Start-Sleep -Seconds 6
try {
  $who = Invoke-RestMethod -Uri 'http://127.0.0.1:7717/whoami' -TimeoutSec 5
  Ok "Worker is up: $($who.hostname)  id=$($who.workerId)  gpu=$(if ($who.gpu) { $who.gpu } else { 'none (CPU only)' })"
  Write-Host "`nOpen $StudioUrl on this machine and it will render here.`n" -ForegroundColor White
} catch {
  Warn "Worker did not answer on 127.0.0.1:7717 yet. Check worker\worker.log."
}

if (-not $WithMusicRemoval) {
  Write-Host "Note: music removal (Demucs) is not installed. Re-run with -WithMusicRemoval to add it." -ForegroundColor DarkGray
}
