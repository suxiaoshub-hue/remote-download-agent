param([Parameter(Mandatory=$true)][string]$Bin)
$ErrorActionPreference = 'Stop'
$Bin = (Resolve-Path $Bin).Path
if (!(Test-Path (Join-Path $Bin 'PcstoryCommandTest.exe'))) { throw 'Direct-download command is not implemented yet' }
$root = Join-Path $env:RUNNER_TEMP 'pcstory-command-tests'
New-Item -ItemType Directory -Force $root | Out-Null
foreach ($mode in @('rotation', 'started', 'accepted', 'failed', 'quiet', 'wrong-gid', 'timeout')) {
    $folder = Join-Path $root ('中文 路径-' + $mode + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force $folder | Out-Null
    $fixture = Start-Process (Join-Path $Bin 'PcstoryCommandFixture.exe') -ArgumentList @('"' + $folder + '"', $mode) -PassThru
    try {
        $deadline = (Get-Date).AddSeconds(10)
        while (!(Test-Path (Join-Path $folder 'ready.txt'))) {
            if ($fixture.HasExited -or (Get-Date) -gt $deadline) { throw "Fixture failed: $mode" }
            Start-Sleep -Milliseconds 50
        }
        $result = Join-Path $folder 'result.txt'
        & (Join-Path $Bin 'PcstoryCommandTest.exe') --game-id 5131 --pid $fixture.Id --log-dir (Join-Path $folder 'log') --wait-seconds 1 --message-timeout-ms 100 --output $result
        $code = $LASTEXITCODE
        $expected = switch ($mode) { 'started' {0} 'accepted' {2} 'failed' {3} 'timeout' {5} default {4} }
        if ($code -ne $expected) { throw "Expected $expected, got $code for $mode" }
        if ($mode -eq 'timeout') { Start-Sleep -Milliseconds 1300 }
        if ((Get-Content (Join-Path $folder 'parameters.txt') -Raw).Trim() -ne '5131 0 0 0') {
            throw "Remote memory parameters differ: $mode"
        }
        if ($fixture.HasExited) { throw "Target crashed: $mode" }
        if ($mode -eq 'started') {
            if ((Get-Content $result -Raw) -notmatch 'STARTED') { throw 'Start evidence missing' }
        }
        & (Join-Path $Bin 'PcstoryAdapter.exe') --game-id 5131 --pid $fixture.Id --output (Join-Path $folder 'rejected.txt')
        if ($LASTEXITCODE -ne 1) { throw 'Production build accepted fixture' }
        Write-Host "PASS $mode (exit $code); production rejects fixture"
    } finally {
        if (!$fixture.HasExited) { Stop-Process -Id $fixture.Id }
    }
}
$invalidReport = Join-Path $root 'invalid.txt'
foreach ($invalidValue in @('0', 'abc', '8049')) {
    Set-Content -Path $invalidReport -Value 'RESULT=STARTED Previous invocation'
    & (Join-Path $Bin 'PcstoryAdapter.exe') --game-id $invalidValue --output $invalidReport
    if ($LASTEXITCODE -ne 1) { throw 'Invalid input was accepted' }
    $text = Get-Content $invalidReport -Raw
    if ($text -notmatch 'RESULT=ERROR' -or $text -match 'STARTED') { throw 'Stale success survives invalid input' }
    Write-Host "PASS invalid input $invalidValue replaces previous result"
}
$global:LASTEXITCODE = 0
