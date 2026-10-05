param([string]$Bin)
$ErrorActionPreference = 'Stop'
$Bin = (Resolve-Path $Bin).Path
$debugger = Join-Path $Bin 'PcstoryDebugger.exe'
if (!(Test-Path $debugger)) { throw 'Debugger not implemented: PcstoryDebugger.exe missing' }
$testDir = Join-Path $env:RUNNER_TEMP 'pcstory-capture-smoke'
New-Item -ItemType Directory -Force $testDir | Out-Null
Get-ChildItem $testDir -File | Remove-Item -Force
$fixture = $null
$capture = $null
function Wait-File([string]$path, [int]$seconds = 15) {
    $deadline = [DateTime]::UtcNow.AddSeconds($seconds)
    while (!(Test-Path $path)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw "Timed out waiting for $path" }
        Start-Sleep -Milliseconds 100
    }
}
try {
    $fixture = Start-Process (Join-Path $Bin 'CaptureFixture.exe') -WorkingDirectory $testDir -PassThru
    Wait-File (Join-Path $testDir 'ready.txt')
    $log = Join-Path $testDir 'capture.txt'
    $capture = Start-Process $debugger -ArgumentList @('--pid', $fixture.Id, '--seconds', '8', '--output', $log, '--no-pause') -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 100
        if ($capture.HasExited) { throw "Debugger exited early: $($capture.ExitCode)" }
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Capture never became ready' }
        $text = if (Test-Path $log) { Get-Content $log -Raw } else { '' }
    } until ($text -match 'CAPTURE_READY')
    Set-Content (Join-Path $testDir 'go.txt') 'go'
    Wait-File (Join-Path $testDir 'calls.txt')
    if (!$capture.WaitForExit(20000)) { throw 'Debugger did not detach within timeout' }
    if ($capture.ExitCode -ne 0) { throw "Debugger failed: $($capture.ExitCode)" }
    $text = Get-Content $log -Raw
    foreach ($expected in @('API=SendMessageW', 'API=PostMessageW', 'API=send ', 'API=WSASend', 'msg=0x8011', 'wparam=0x140b', '504353544f52595f434150545552455f54455354', '504353544f52595f4e45575f5448524541445f54455354', 'DETACHED restored=true')) {
        if (!$text.Contains($expected)) { throw "Missing capture evidence: $expected`n$text" }
    }
    if ($fixture.HasExited) { throw 'Target exited during debugging' }
    Set-Content (Join-Path $testDir 'check.txt') 'check'
    Wait-File (Join-Path $testDir 'restored.txt')
    Set-Content (Join-Path $testDir 'stop.txt') 'stop'
    if (!$fixture.WaitForExit(5000) -or $fixture.ExitCode -ne 0) { throw 'Target did not exit normally after detach' }
    Write-Output 'PASS: four APIs, window arguments, socket buffers, new thread, restored registers and surviving target'
} finally {
    if ($capture -and !$capture.HasExited) { Stop-Process -Id $capture.Id -Force }
    if ($fixture -and !$fixture.HasExited) { Stop-Process -Id $fixture.Id -Force }
}
