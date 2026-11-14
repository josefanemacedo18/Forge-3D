<#
.SYNOPSIS
    Compila o plugin e monta o pacote de instalação (pasta e .zip).
    Requer o .NET 10 SDK. Uso: pwsh installer/package.ps1 [-Saida dist]
#>
[CmdletBinding()]
param([string]$Saida = "dist", [switch]$SemTestes)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not $SemTestes) {
        dotnet test src/Forge3D.Core.Tests/Forge3D.Core.Tests.csproj -c Release
        if ($LASTEXITCODE -ne 0) { throw 'Testes do núcleo falharam.' }
    }
    dotnet build src/Forge3D.Revit/Forge3D.Revit.csproj -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar o plugin.' }

    $pkg = Join-Path $Saida 'Forge3D'
    if (Test-Path $pkg) { Remove-Item $pkg -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $pkg | Out-Null
    $bin = 'src/Forge3D.Revit/bin/Release/net10.0-windows'
    $addin = Join-Path $pkg 'addin'
    New-Item -ItemType Directory -Force -Path $addin | Out-Null
    foreach ($f in @('Forge3D.Revit.dll', 'Forge3D.Revit.pdb', 'Forge3D.Core.dll', 'Forge3D.Core.pdb', 'Forge3D.Revit.deps.json', 'Forge3D.addin')) {
        Copy-Item (Join-Path $bin $f) $addin
    }
    Copy-Item (Join-Path $bin 'diagnostico') $addin -Recurse
    Copy-Item (Join-Path $bin 'exporters') $addin -Recurse

    $engine = Join-Path $pkg 'engine'
    New-Item -ItemType Directory -Force -Path $engine | Out-Null
    Copy-Item 'engine/forge3d' $engine -Recurse
    Get-ChildItem $engine -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    foreach ($f in @('pyproject.toml', 'requirements.txt', 'requirements-ia.txt', 'README.md')) { Copy-Item "engine/$f" $engine }

    $inst = Join-Path $pkg 'installer'
    New-Item -ItemType Directory -Force -Path $inst | Out-Null
    Copy-Item 'installer/install.ps1', 'installer/uninstall.ps1' $inst
    Copy-Item 'installer/Instalar.cmd', 'installer/Desinstalar.cmd' $pkg
    Copy-Item 'docs/GUIA-DO-USUARIO.md' $pkg -ErrorAction SilentlyContinue
    Copy-Item 'README.md' $pkg

    $zip = Join-Path $Saida 'Forge3D-Revit2027.zip'
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $pkg -DestinationPath $zip
    Write-Host "Pacote pronto: $zip" -ForegroundColor Green
} finally {
    Pop-Location
}
