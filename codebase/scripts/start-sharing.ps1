param([int]$Port = 3001, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$appDirectory = Split-Path $PSScriptRoot -Parent
$repoDirectory = Split-Path $appDirectory -Parent
$workspaceDirectory = Split-Path $repoDirectory -Parent
$localDirectory = Join-Path $workspaceDirectory '.local'
$logDirectory = Join-Path $localDirectory 'logs'
$statePath = Join-Path $localDirectory 'sharing-state.json'
$accessPath = Join-Path $localDirectory 'share-access.json'
$tunnelBinary = Join-Path $localDirectory 'bin/cloudflared.exe'
$pythonBinary = Join-Path $appDirectory '.venv/Scripts/python.exe'

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $tunnelBinary -Parent) | Out-Null
function Start-SharingSleepBlocker([int]$TunnelProcessId, [int]$GatewayProcessId) {
    $awakeScript = Join-Path $PSScriptRoot 'keep-sharing-awake.ps1'
    Start-Process -FilePath "$PSHOME/powershell.exe" -WindowStyle Hidden `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$awakeScript`" -TunnelProcessId $TunnelProcessId -GatewayProcessId $GatewayProcessId" | Out-Null
}
if (Test-Path -LiteralPath $statePath) {
    $previousState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if (Get-Process -Id $previousState.tunnel_pid -ErrorAction SilentlyContinue) {
        Start-SharingSleepBlocker $previousState.tunnel_pid $previousState.gateway_pid
        Write-Host "Sharing is already running: $($previousState.url)"
        Write-Host "Sharing credentials: $accessPath"
        exit 0
    }
    & (Join-Path $PSScriptRoot 'stop-sharing.ps1')
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Choose another sharing port."
}

if (!(Test-Path -LiteralPath $tunnelBinary)) {
    Write-Host 'Downloading the official Cloudflare tunnel client...'
    $release = Invoke-RestMethod 'https://api.github.com/repos/cloudflare/cloudflared/releases/latest'
    $asset = $release.assets | Where-Object name -eq 'cloudflared-windows-amd64.exe' | Select-Object -First 1
    if (!$asset) { throw 'Official Windows tunnel download was not found.' }
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $tunnelBinary
    if ($asset.digest -and $asset.digest.StartsWith('sha256:')) {
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $tunnelBinary).Hash.ToLowerInvariant()
        if ($actualHash -ne $asset.digest.Substring(7)) { throw 'Tunnel download checksum did not match.' }
    }
}

& (Join-Path $workspaceDirectory 'start-app.ps1') -NoBrowser
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'App startup failed.' }
if (!$SkipBuild) {
    Push-Location (Join-Path $appDirectory 'ui-react')
    try {
        & 'C:/Program Files/nodejs/npm.cmd' run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
if (!(Test-Path -LiteralPath (Join-Path $appDirectory 'ui-react/dist/index.html'))) { throw 'Build the frontend before sharing.' }

function New-SharingSecret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
$sharingPassword = New-SharingSecret
$env:COPILOT_SHARE_USERNAME = 'share'
$env:COPILOT_SHARE_PASSWORD = $sharingPassword
$env:COPILOT_SHARE_SESSION_KEY = New-SharingSecret
$env:COPILOT_SHARE_PORT = "$Port"
$gatewayProcess = $null
$tunnelProcess = $null
try {
    $gatewayProcess = Start-Process -FilePath $pythonBinary -ArgumentList '-m scripts.share_app' `
        -WorkingDirectory $appDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDirectory 'sharing-gateway.out.log') `
        -RedirectStandardError (Join-Path $logDirectory 'sharing-gateway.err.log')
    $gatewayReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/" | Out-Null
        } catch {
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) { $gatewayReady = $true; break }
        }
        Start-Sleep -Seconds 1
    }
    if (!$gatewayReady) { throw 'Sharing gateway did not start. Check sharing-gateway.err.log.' }
    $tunnelLog = Join-Path $logDirectory 'sharing-tunnel.err.log'
    $tunnelProcess = Start-Process -FilePath $tunnelBinary `
        -ArgumentList "tunnel --url http://127.0.0.1:$Port --no-autoupdate --protocol http2" `
        -WorkingDirectory $workspaceDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDirectory 'sharing-tunnel.out.log') -RedirectStandardError $tunnelLog
    $sharingUrl = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-Path -LiteralPath $tunnelLog) {
            $tunnelText = Get-Content -LiteralPath $tunnelLog -Raw
            if ($tunnelText -match 'https://[a-z0-9-]+\.trycloudflare\.com') { $sharingUrl = $Matches[0]; break }
        }
        if ($tunnelProcess.HasExited) { throw 'The tunnel stopped. Check sharing-tunnel.err.log.' }
        Start-Sleep -Seconds 1
    }
    if (!$sharingUrl) { throw 'The tunnel did not provide a link. Check sharing-tunnel.err.log.' }
    @{url=$sharingUrl; username='share'; password=$sharingPassword; note='First enter these sharing credentials, then sign in with your app account.'} |
        ConvertTo-Json | Set-Content -LiteralPath $accessPath -Encoding UTF8
    @{url=$sharingUrl; gateway_pid=$gatewayProcess.Id; tunnel_pid=$tunnelProcess.Id; port=$Port; started_at=(Get-Date).ToUniversalTime().ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Start-SharingSleepBlocker $tunnelProcess.Id $gatewayProcess.Id
    Write-Host "Sharing link: $sharingUrl"
    Write-Host "Sharing credentials: $accessPath"
    Write-Host 'Idle sleep is blocked while sharing runs. Keep this laptop powered and online. Run stop-sharing.ps1 to close access.'
} catch {
    if ($tunnelProcess) { Stop-Process -Id $tunnelProcess.Id -ErrorAction SilentlyContinue }
    if ($gatewayProcess) {
        Get-CimInstance Win32_Process | Where-Object ParentProcessId -eq $gatewayProcess.Id | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
        Stop-Process -Id $gatewayProcess.Id -ErrorAction SilentlyContinue
    }
    throw
} finally {
    Remove-Item Env:COPILOT_SHARE_USERNAME, Env:COPILOT_SHARE_PASSWORD, Env:COPILOT_SHARE_SESSION_KEY, Env:COPILOT_SHARE_PORT -ErrorAction SilentlyContinue
}
