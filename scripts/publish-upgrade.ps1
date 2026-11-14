# Run in normal PowerShell, using the existing checkout and Git credentials.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$branch = 'upgrade/approval-hardware'
$base = '793c515f878feaad567414bf22d536bc701b8554'
$files = @(
    'lifetime_agent.py', 'lifetime_connect.py', 'lifetime_i18n.py',
    'lifetime_ledger.py', 'lifetime_server_gui.py', 'lifetime_server_widgets.py',
    'lifetime_signals.py', 'lifetime_system_gui.py', 'lifetime_tracker.py',
    'restart_desktop.ps1', 'restart_server_gui.ps1', 'test_company_gui.py',
    'test_ledger_recovery.py', 'test_server_gui.py', 'test_signals.py',
    'docs/AWAKE_ACCOUNTING_INVESTIGATION.md', 'launch-server.ps1',
    'test_awake_clock_rollback.py', 'scripts/try-upgrade.ps1',
    'scripts/start-server-elevated.ps1', 'scripts/manual_server_runtime.py',
    'scripts/test_manual_launch.py', 'scripts/test_restart_helpers.ps1',
    'scripts/README.md', 'scripts/publish-upgrade.ps1'
)
Push-Location -LiteralPath $repo
try {
    $headSha = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $headSha -ne $base) {
        throw 'HEAD changed from the reviewed base 793c515. Nothing was staged or published.'
    }
    $staged = @(git diff --cached --name-only)
    if ($LASTEXITCODE -ne 0 -or $staged.Count) {
        throw 'The Git index already contains staged changes or cannot be read. Nothing was changed.'
    }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed.' }
    git show-ref --verify --quiet "refs/heads/$branch"
    if ($LASTEXITCODE -eq 0) {
        $branchSha = git rev-parse $branch
        if ($branchSha -ne $base) { throw 'The target branch already has other commits. Nothing was changed.' }
        git switch $branch
    } else {
        git switch -c $branch
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not select the upgrade branch.' }
    git add -- $files
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage the upgrade files.' }
    $unexpected = @(git diff --cached --name-only | Where-Object { $_ -notin $files })
    if ($unexpected.Count) { throw 'Unexpected staged files; no commit or push was attempted.' }
    git commit -m 'Improve accounting, connections and hardware details'
    if ($LASTEXITCODE -ne 0) { throw 'Commit failed. Nothing was pushed.' }
    git push -u origin $branch
    if ($LASTEXITCODE -ne 0) { throw 'Commit exists locally, but push failed. Retry: git push -u origin upgrade/approval-hardware' }
    Write-Host "Published branch: $branch"
    git log -1 --format='%h %s'
    Write-Host 'https://github.com/jpl-innovation/Lifetime/tree/upgrade/approval-hardware'
} finally {
    Pop-Location
}
