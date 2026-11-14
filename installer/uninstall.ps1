<#
.SYNOPSIS
    Remove o Forge 3D do Revit 2027.
.PARAMETER Tudo
    Remove também o motor, o Python, os modelos de IA e a configuração.
    As famílias geradas (Documentos\Forge3D) nunca são apagadas.
#>
[CmdletBinding()]
param([switch]$Tudo)

$ErrorActionPreference = 'Stop'
if (Get-Process -Name 'Revit' -ErrorAction SilentlyContinue) {
    Write-Host 'Feche o Revit antes de desinstalar.' -ForegroundColor Red
    exit 1
}
$AddinRoot = Join-Path $env:APPDATA 'Autodesk\Revit\Addins\2027'
$manifest = Join-Path $AddinRoot 'Forge3D.addin'
$addinDir = Join-Path $AddinRoot 'Forge3D'
if (Test-Path $manifest) { Remove-Item $manifest -Force }
if (Test-Path $addinDir) { Remove-Item $addinDir -Recurse -Force }
Write-Host 'Plugin removido do Revit 2027.' -ForegroundColor Green

if (-not $Tudo) {
    $resp = Read-Host 'Remover também o motor, o Python e os modelos de IA? (S/N)'
    if ($resp -match '^[sSyY]') { $Tudo = $true }
}
if ($Tudo) {
    $local = Join-Path $env:LOCALAPPDATA 'Forge3D'
    if (Test-Path $local) { Remove-Item $local -Recurse -Force }
    $cfg = Join-Path $env:APPDATA 'Forge3D'
    if (Test-Path $cfg) { Remove-Item $cfg -Recurse -Force }
    Write-Host 'Motor, Python, modelos e configuração removidos.' -ForegroundColor Green
}
Write-Host 'As famílias geradas em Documentos\Forge3D foram mantidas.'
