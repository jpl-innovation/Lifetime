param(
    [switch]$ElevationAttempted,
    [string]$OriginalUser,
    [string]$OriginalLocalAppData,
    [string]$OriginalAppData,
    [string]$OriginalPython
)
$InstallerPath = $PSCommandPath
$ExePackage = $true
. (Join-Path $PSScriptRoot 'install_windows_common.ps1')
