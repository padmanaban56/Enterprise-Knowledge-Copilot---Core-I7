$ErrorActionPreference = 'Stop'
$appDirectory = Split-Path $PSScriptRoot -Parent
$workspaceDirectory = Split-Path (Split-Path $appDirectory -Parent) -Parent
$statePath = Join-Path $workspaceDirectory '.local/sharing-state.json'
if (!(Test-Path -LiteralPath $statePath)) { Write-Host 'Sharing is not running.'; exit 0 }
$sharingState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$processSnapshot = @(Get-CimInstance Win32_Process)
foreach ($sharingPid in @($sharingState.tunnel_pid, $sharingState.gateway_pid)) {
    $sharingProcess = $processSnapshot | Where-Object ProcessId -eq $sharingPid | Select-Object -First 1
    if (!$sharingProcess) { continue }
    $isGateway = $sharingProcess.CommandLine -like '*-m scripts.share_app*'
    $isTunnel = $sharingProcess.ExecutablePath -eq [IO.Path]::GetFullPath((Join-Path $workspaceDirectory '.local/bin/cloudflared.exe'))
    if (!$isGateway -and !$isTunnel) { throw "Process $sharingPid no longer belongs to sharing; refusing to stop it." }
    $processSnapshot | Where-Object ParentProcessId -eq $sharingPid | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
    Stop-Process -Id $sharingPid -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $statePath
$accessPath = Join-Path $workspaceDirectory '.local/share-access.json'
if (Test-Path -LiteralPath $accessPath) { Remove-Item -LiteralPath $accessPath }
Write-Host 'Sharing stopped. The local app remains running.'
