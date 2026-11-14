$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
function Get-Process { param($Id, $ErrorAction) return $script:fakeProcess }
function Stop-Process { param($InputObject, [switch]$Force, $ErrorAction) $script:stops++; if (-not $Force) { throw 'Expected Force' } }
$cases = 0
foreach ($name in @('restart_server_gui.ps1','restart_desktop.ps1')) {
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $repo $name),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    $loop=$ast.Find({param($node) $node -is [Management.Automation.Language.ForEachStatementAst]},$true)
    $body=[scriptblock]::Create($loop.Extent.Text)
    foreach ($state in @('running','exited','missing','timeout')) {
        $script:stops=0; $script:waited=0; $script:disposed=0
        $script:fakeProcess=[pscustomobject]@{Handle=1;HasExited=($state -eq 'exited');WaitResult=($state -ne 'timeout')}
        $script:fakeProcess | Add-Member ScriptMethod WaitForExit {param($milliseconds) if($milliseconds -ne 15000){throw 'Incorrect timeout'}; $script:waited++; return $this.WaitResult}
        $script:fakeProcess | Add-Member ScriptMethod Dispose {$script:disposed++}
        if($state -eq 'missing'){$script:fakeProcess=$null}
        $guis=@([pscustomobject]@{ProcessId=123})
        $failed=$false
        try { & $body } catch { if($_ -notmatch 'did not exit within 15 seconds'){throw}; $failed=$true }
        if($failed -ne ($state -eq 'timeout')){throw "Wrong outcome: $name $state"}
        $active=$state -in @('running','timeout')
        if($script:stops -ne [int]$active -or $script:waited -ne [int]$active){throw "Incorrect stop/wait: $name $state"}
        if($script:disposed -ne [int]($state -ne 'missing')){throw "Handle not disposed: $name $state"}
        $cases++
    }
}
Write-Output "$cases mocked restart checks passed. No real processes were stopped or launched."
