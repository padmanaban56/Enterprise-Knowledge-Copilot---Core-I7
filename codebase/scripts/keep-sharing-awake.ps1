param(
    [Parameter(Mandatory = $true)][int]$TunnelProcessId,
    [Parameter(Mandatory = $true)][int]$GatewayProcessId
)
$ErrorActionPreference = 'Stop'

# Keep the Windows power request on one native thread for its entire lifetime.
# This prevents idle sleep, while allowing display sleep and explicit user sleep.
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

public static class CopilotSharingAwake
{
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint SetThreadExecutionState(uint flags);

    public static void Run(int tunnelId, int gatewayId, string statusPath)
    {
        bool created;
        using (var mutex = new Mutex(false, @"Local\CopilotSharingAwake-" + tunnelId, out created))
        {
            if (!created) return;
            using (var tunnel = Process.GetProcessById(tunnelId))
            using (var gateway = Process.GetProcessById(gatewayId))
            {
                if (tunnel.ProcessName != "cloudflared" || gateway.ProcessName != "python")
                    throw new InvalidOperationException("Expected the sharing tunnel and Python gateway.");
                if (SetThreadExecutionState(0x80000001) == 0)
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                try
                {
                    File.WriteAllText(statusPath, "{\"active\":true,\"process_id\":" +
                        Process.GetCurrentProcess().Id + ",\"tunnel_pid\":" + tunnelId +
                        ",\"gateway_pid\":" + gatewayId + "}");
                    while (!tunnel.WaitForExit(5000) && !gateway.HasExited) { }
                }
                finally
                {
                    SetThreadExecutionState(0x80000000);
                    File.Delete(statusPath);
                }
            }
        }
    }
}
'@
$workspaceDirectory = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$statusPath = Join-Path $workspaceDirectory ".local/sharing-awake-$TunnelProcessId.json"
[CopilotSharingAwake]::Run($TunnelProcessId, $GatewayProcessId, $statusPath)
