<#
.SYNOPSIS
    Compila o plugin e monta o pacote de instalação completo (pasta e .zip).

.DESCRIPTION
    O pacote já traz o motor pronto: Python 3.12 embutido com todas as
    bibliotecas instaladas e pré-compiladas, num único engine.zip. A instalação
    no computador do usuário só copia arquivos — sem downloads, em segundos.

    Requer o .NET 10 SDK e um Python 3.12 (usado só para baixar as bibliotecas
    para Windows e pré-compilá-las; funciona também fora do Windows).

    Uso: pwsh installer/package.ps1 [-Saida dist] [-SemTestes] [-Python python]
#>
[CmdletBinding()]
param(
    [string]$Saida = "dist",
    [switch]$SemTestes,
    [string]$Python = "",
    [string]$VersaoPython = "3.12.10"
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $windows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
    if ([string]::IsNullOrWhiteSpace($Python)) { $Python = if ($windows) { 'python' } else { 'python3' } }
    $ver = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($LASTEXITCODE -ne 0 -or $ver -ne '3.12') { throw "É preciso um Python 3.12 para montar o pacote (encontrado: '$ver' em '$Python')." }

    if (-not $SemTestes) {
        dotnet test src/Forge3D.Core.Tests/Forge3D.Core.Tests.csproj -c Release
        if ($LASTEXITCODE -ne 0) { throw 'Testes do núcleo falharam.' }
    }
    dotnet build src/Forge3D.Revit/Forge3D.Revit.csproj -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar o plugin.' }

    New-Item -ItemType Directory -Force -Path $Saida | Out-Null
    $Saida = (Resolve-Path $Saida).Path
    $pkg = Join-Path $Saida 'Forge3D'
    if (Test-Path $pkg) { Remove-Item $pkg -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $pkg | Out-Null

    # --- Plugin ------------------------------------------------------------------------
    $bin = 'src/Forge3D.Revit/bin/Release/net10.0-windows'
    $addin = Join-Path $pkg 'addin'
    New-Item -ItemType Directory -Force -Path $addin | Out-Null
    foreach ($f in @('Forge3D.Revit.dll', 'Forge3D.Revit.pdb', 'Forge3D.Core.dll', 'Forge3D.Core.pdb', 'Forge3D.Revit.deps.json', 'Forge3D.addin')) {
        Copy-Item (Join-Path $bin $f) $addin
    }
    Copy-Item (Join-Path $bin 'diagnostico') $addin -Recurse
    Copy-Item (Join-Path $bin 'exporters') $addin -Recurse

    # --- Motor com Python embutido -----------------------------------------------------
    $stage = Join-Path $Saida 'motor-montagem'
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    Copy-Item 'engine/forge3d' $stage -Recurse
    Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    foreach ($f in @('pyproject.toml', 'requirements.txt', 'requirements-ia.txt', 'README.md')) { Copy-Item "engine/$f" $stage }

    $cache = Join-Path $Saida 'cache'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $embedZip = Join-Path $cache "python-$VersaoPython-embed-amd64.zip"
    if (-not (Test-Path $embedZip)) {
        $url = "https://www.python.org/ftp/python/$VersaoPython/python-$VersaoPython-embed-amd64.zip"
        Write-Host "Baixando o Python embutido: $url"
        Invoke-WebRequest -Uri $url -OutFile $embedZip -UseBasicParsing
    }
    $pyDir = Join-Path $stage 'python'
    Expand-Archive -Path $embedZip -DestinationPath $pyDir -Force

    # Caminhos do Python embutido (relativos a engine\python): biblioteca padrão,
    # bibliotecas do motor, o próprio motor (..) e o módulo de IA opcional, que
    # fica fora da pasta do motor para sobreviver às atualizações do plugin.
    $pth = Get-ChildItem $pyDir -Filter 'python3*._pth' | Select-Object -First 1
    if ($null -eq $pth) { throw 'Arquivo ._pth não encontrado no Python embutido.' }
    $zipName = (Get-ChildItem $pyDir -Filter 'python3*.zip' | Select-Object -First 1).Name
    $lines = @($zipName, '.', 'Lib\site-packages', '..', '..\..\ia\site-packages', 'import site')
    [IO.File]::WriteAllText($pth.FullName, ($lines -join "`r`n") + "`r`n", (New-Object Text.UTF8Encoding($false)))

    $site = Join-Path $pyDir 'Lib\site-packages'
    New-Item -ItemType Directory -Force -Path $site | Out-Null
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-compile', '--only-binary=:all:',
                 '--target', $site, '-r', 'engine/requirements.txt', 'pip')
    if (-not $windows) {
        $pipArgs += @('--platform', 'win_amd64', '--python-version', '3.12', '--implementation', 'cp', '--abi', 'cp312')
    }
    Write-Host 'Instalando as bibliotecas do motor no Python embutido…'
    & $Python @pipArgs
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar as bibliotecas no Python embutido.' }
    # Executáveis de console e testes das bibliotecas não são usados pelo motor.
    $binDir = Join-Path $site 'bin'
    if (Test-Path $binDir) { Remove-Item $binDir -Recurse -Force }
    Get-ChildItem $site -Recurse -Directory -Filter 'tests' | Where-Object { Test-Path $_.FullName } |
        Remove-Item -Recurse -Force

    # Pré-compilação: o motor abre mais rápido (o .pyc vale mesmo se a data do
    # arquivo mudar na cópia).
    & $Python -m compileall -q -j 0 --invalidation-mode unchecked-hash (Join-Path $stage 'forge3d') $site
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao pré-compilar o motor.' }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $engineZip = Join-Path $pkg 'engine.zip'
    [IO.Compression.ZipFile]::CreateFromDirectory($stage, $engineZip, [IO.Compression.CompressionLevel]::Optimal, $false)
    $mb = [math]::Round((Get-Item $engineZip).Length / 1MB, 1)
    Write-Host "Motor empacotado: $engineZip ($mb MB)"

    # --- Instaladores e documentos -----------------------------------------------------
    $inst = Join-Path $pkg 'installer'
    New-Item -ItemType Directory -Force -Path $inst | Out-Null
    Copy-Item 'installer/install.ps1', 'installer/install-ia.ps1', 'installer/uninstall.ps1' $inst
    Copy-Item 'installer/Instalar.cmd', 'installer/InstalarIA.cmd', 'installer/Desinstalar.cmd' $pkg
    Copy-Item 'docs/GUIA-DO-USUARIO.md' $pkg -ErrorAction SilentlyContinue
    Copy-Item 'README.md' $pkg

    $zip = Join-Path $Saida 'Forge3D-Revit2027.zip'
    if (Test-Path $zip) { Remove-Item $zip -Force }
    [IO.Compression.ZipFile]::CreateFromDirectory($pkg, $zip, [IO.Compression.CompressionLevel]::Optimal, $true)
    Write-Host "Pacote pronto: $zip" -ForegroundColor Green
} finally {
    Pop-Location
}
