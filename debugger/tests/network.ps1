param([string]$Bin)
$ErrorActionPreference = 'Stop'
$Bin = (Resolve-Path $Bin).Path
$testDir = Join-Path $env:RUNNER_TEMP 'pcstory-capture-network'
New-Item -ItemType Directory -Force $testDir | Out-Null
$fixture = $null
$capture = $null
try {
    $fixture = Start-Process (Join-Path $Bin 'CaptureFixture.exe') -ArgumentList '--network' -WorkingDirectory $testDir -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (!(Test-Path (Join-Path $testDir 'ready.txt'))) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Fixture not ready' }
        Start-Sleep -Milliseconds 100
    }
    $log = Join-Path $testDir 'network.txt'
    $capture = Start-Process (Join-Path $Bin 'PcstoryDebugger.exe') -ArgumentList @('--network', '--pid', $fixture.Id, '--seconds', '6', '--output', $log, '--no-pause') -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 100
        if ($capture.HasExited) { throw 'Debugger exited early' }
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Capture not ready' }
        $text = if (Test-Path $log) { Get-Content $log -Raw } else { '' }
    } until ($text -match 'CAPTURE_READY')
    Set-Content (Join-Path $testDir 'go.txt') 'go'
    if (!$capture.WaitForExit(15000) -or $capture.ExitCode -ne 0) { throw 'Network debugger failed' }
    $text = Get-Content $log -Raw
    foreach ($pattern in @('API=connect tid=.*addr=127\.0\.0\.1:\d+', 'RETURN API=connect .*result=0', 'RETURN API=send .*result=22', 'RETURN API=recv .*result=22 .*hex=504353544f52595f5245504c59206769643d3531333100', 'RETURN API=recv .*result=-1', 'PORT_SCAN', 'DETACHED restored=true')) {
        if ($text -notmatch $pattern) { throw "Missing $pattern`n$text" }
    }
    if ($fixture.HasExited) { throw 'Fixture died during capture' }
    Set-Content (Join-Path $testDir 'check.txt') 'check'
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while (!(Test-Path (Join-Path $testDir 'restored.txt'))) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Registers not restored' }
        Start-Sleep -Milliseconds 100
    }
    Set-Content (Join-Path $testDir 'stop.txt') 'stop'
    if (!$fixture.WaitForExit(5000) -or $fixture.ExitCode -ne 0) { throw 'Fixture exit failed' }
    Write-Output 'PASS: network endpoints, connect/send/recv results, actual reply bytes, failed receive and detach'
} finally {
    if ($capture -and !$capture.HasExited) { Stop-Process -Id $capture.Id -Force }
    if ($fixture -and !$fixture.HasExited) { Stop-Process -Id $fixture.Id -Force }
}
