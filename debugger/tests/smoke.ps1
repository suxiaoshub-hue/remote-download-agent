param([string]$Bin, [switch]$Interrupt, [switch]$Fault)
$ErrorActionPreference = 'Stop'
$Bin = (Resolve-Path $Bin).Path
$debugger = Join-Path $Bin $(if ($Fault) { 'CaptureFaultTest.exe' } else { 'PcstoryDebugger.exe' })
if (!(Test-Path $debugger)) { throw 'Debugger not implemented: PcstoryDebugger.exe missing' }
$testDir = Join-Path $env:RUNNER_TEMP $(if ($Fault) { 'pcstory-capture-fault' } elseif ($Interrupt) { 'pcstory-capture-interrupt' } else { 'pcstory-capture-smoke' })
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
    $refusedLog = Join-Path $testDir 'refused.txt'
    $refused = Start-Process $debugger -ArgumentList @('--pid', $fixture.Id, '--seconds', '5', '--output', $refusedLog, '--no-pause') -PassThru
    if (!$refused.WaitForExit(5000) -or $refused.ExitCode -eq 0) { throw 'Second debugger was not rejected' }
    if ((Get-Content $refusedLog -Raw) -notmatch 'already has a debugger') { throw 'Wrong refusal reason' }
    Set-Content (Join-Path $testDir 'go.txt') 'go'
    Wait-File (Join-Path $testDir 'calls.txt')
    if ($Interrupt) {
        $signal = Start-Process (Join-Path $Bin 'CaptureFixture.exe') -ArgumentList @('--interrupt', $capture.Id) -PassThru
        if (!$signal.WaitForExit(5000) -or $signal.ExitCode -ne 0) { throw 'Ctrl+C could not be delivered' }
    }
    if (!$capture.WaitForExit(20000)) { throw 'Debugger did not detach within timeout' }
    if (!$Fault -and $capture.ExitCode -ne 0) { throw "Debugger failed: $($capture.ExitCode)" }
    if ($Fault -and $capture.ExitCode -eq 0) { throw 'Injected failure was not reported' }
    $text = Get-Content $log -Raw
    if ($Interrupt -and !$text.Contains('STOP reason=Ctrl+C')) { throw 'Ctrl+C was not handled' }
    if ($Fault) {
        foreach ($expected in @('Injected API capture failure', 'RESTORE_RETRY injected=true', 'DETACHED restored=true')) {
            if (!$text.Contains($expected)) { throw "Missing failure recovery evidence: $expected`n$text" }
        }
    } else {
        foreach ($expected in @('\] API=SendMessageW tid=\d+ .*msg=0x4a .*copydata.tag=0x140b .*hex=504353544f52595f434150545552455f54455354', '\] API=PostMessageW tid=\d+ .*msg=0x8011 wparam=0x140b lparam=0x2a', '\] API=send tid=\d+ .*hex=504353544f52595f434150545552455f54455354', '\] API=WSASend tid=\d+ .*buffer\[0\]\.len=\d+ .*hex=504353544f52595f434150545552455f54455354', '\] API=send tid=\d+ .*hex=504353544f52595f4e45575f5448524541445f54455354', 'DETACHED restored=true')) {
            if ($text -notmatch $expected) { throw "Missing capture evidence: $expected`n$text" }
        }
    }
    if ($fixture.HasExited) { throw 'Target exited during debugging' }
    Set-Content (Join-Path $testDir 'check.txt') 'check'
    Wait-File (Join-Path $testDir 'restored.txt')
    Set-Content (Join-Path $testDir 'stop.txt') 'stop'
    if (!$fixture.WaitForExit(5000) -or $fixture.ExitCode -ne 0) { throw 'Target did not exit normally after detach' }
    Write-Output "PASS: capture/detach, preserved original registers, debugger exclusion and surviving target; interrupt=$Interrupt fault=$Fault"
} finally {
    if ($capture -and !$capture.HasExited) { Stop-Process -Id $capture.Id -Force }
    if ($fixture -and !$fixture.HasExited) { Stop-Process -Id $fixture.Id -Force }
}
