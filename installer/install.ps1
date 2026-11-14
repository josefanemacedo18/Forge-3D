<#
.SYNOPSIS
    Instala o Forge 3D para o Revit 2027 (instalação rápida, sem downloads).

.DESCRIPTION
    1. Copia o plugin para %APPDATA%\Autodesk\Revit\Addins\2027.
    2. Extrai o motor pronto (Python 3.12 embutido com as bibliotecas já
       instaladas) para %LOCALAPPDATA%\Forge3D\engine.
    3. Grava a configuração e confere o motor.

    Não usa a internet e não pede permissão de administrador. Ao abrir o
    Revit, o plugin já está pronto: o motor é iniciado em segundo plano.

    O módulo de IA 3D (opcional, ~4 GB) é instalado à parte, pelo InstalarIA.cmd,
    e continua instalado quando o plugin é atualizado.

.PARAMETER Origem
    Pasta do pacote (contém "addin" e "engine.zip"). Padrão: pasta acima deste script.

.PARAMETER SemTeste
    Não confere o motor no final.
#>
[CmdletBinding()]
param(
    [string]$Origem = "",
    [switch]$SemTeste
)

$ErrorActionPreference = 'Stop'
$env:PYTHONNOUSERSITE = '1'  # motor isolado de pacotes 'pip --user'
$ProgressPreference = 'SilentlyContinue'

function Etapa([string]$texto) { Write-Host ""; Write-Host "==> $texto" -ForegroundColor Cyan }
function Ok([string]$texto) { Write-Host "    $texto" -ForegroundColor Green }
function Aviso([string]$texto) { Write-Host "    AVISO: $texto" -ForegroundColor Yellow }
function Falha([string]$texto) {
    Write-Host ""
    Write-Host "ERRO: $texto" -ForegroundColor Red
    Write-Host "A instalação não foi concluída." -ForegroundColor Red
    exit 1
}

$relogio = [Diagnostics.Stopwatch]::StartNew()
if ([string]::IsNullOrWhiteSpace($Origem)) { $Origem = Split-Path -Parent $PSScriptRoot }
$Origem = (Resolve-Path $Origem).Path

$Local = Join-Path $env:LOCALAPPDATA 'Forge3D'
$EngineDir = Join-Path $Local 'engine'
$ModelsDir = Join-Path $Local 'models'
$AddinRoot = Join-Path $env:APPDATA 'Autodesk\Revit\Addins\2027'
$AddinDir = Join-Path $AddinRoot 'Forge3D'
$ConfigDir = Join-Path $env:APPDATA 'Forge3D'
$ConfigPath = Join-Path $ConfigDir 'config.json'
$py = Join-Path $EngineDir 'python\python.exe'

Write-Host "Forge 3D — instalação para o Revit 2027" -ForegroundColor White
Write-Host "Pacote: $Origem"

# --- 0. Verificações ---------------------------------------------------------------------
if (Get-Process -Name 'Revit' -ErrorAction SilentlyContinue) { Falha 'Feche o Revit antes de instalar (o plugin em uso não pode ser substituído).' }
$pkgAddin = Join-Path $Origem 'addin'
$engineZip = Join-Path $Origem 'engine.zip'
if (-not (Test-Path (Join-Path $pkgAddin 'Forge3D.Revit.dll'))) { Falha "Pacote incompleto: não encontrei $pkgAddin\Forge3D.Revit.dll. Extraia o .zip inteiro antes de instalar." }
if (-not (Test-Path $engineZip)) { Falha "Pacote incompleto: não encontrei $engineZip. Extraia o .zip inteiro antes de instalar." }

# Motor de uma sessão anterior ainda aberto (o Revit fechou de forma inesperada).
Get-Process -Name 'python' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($EngineDir, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue

# --- 1. Plugin ---------------------------------------------------------------------------
Etapa 'Plugin do Revit 2027'
if (Test-Path $AddinDir) { Remove-Item -Path $AddinDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $AddinDir | Out-Null
Get-ChildItem -Path $pkgAddin | Where-Object { $_.Name -ne 'Forge3D.addin' } | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $AddinDir -Recurse -Force
}
# Arquivos baixados da internet vêm "bloqueados" pelo Windows.
Get-ChildItem -Path $AddinDir -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
$manifest = Get-Content -Path (Join-Path $pkgAddin 'Forge3D.addin') -Raw -Encoding UTF8
$dll = Join-Path $AddinDir 'Forge3D.Revit.dll'
$manifest = $manifest -replace '<Assembly>[^<]*</Assembly>', "<Assembly>$dll</Assembly>"
New-Item -ItemType Directory -Force -Path $AddinRoot | Out-Null
[IO.File]::WriteAllText((Join-Path $AddinRoot 'Forge3D.addin'), $manifest, (New-Object Text.UTF8Encoding($false)))
Ok "Plugin em $AddinDir"

# --- 2. Motor (pronto, sem downloads) ----------------------------------------------------
Etapa 'Motor do Forge 3D'
if (Test-Path $EngineDir) {
    try {
        Remove-Item -Path $EngineDir -Recurse -Force
    } catch {
        Falha "Não foi possível substituir o motor em $EngineDir (algum arquivo em uso). Reinicie o computador e rode o instalador de novo."
    }
}
New-Item -ItemType Directory -Force -Path $EngineDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
try {
    [IO.Compression.ZipFile]::ExtractToDirectory($engineZip, $EngineDir)
} catch {
    Falha "Não foi possível extrair o motor: $($_.Exception.Message)"
}
if (-not (Test-Path $py)) { Falha "O pacote não contém o Python do motor ($py)." }
Ok "Motor em $EngineDir"

# Sobras da instalação antiga (Python baixado pelo uv): não são mais usadas.
foreach ($velho in @('python', 'cache', 'tools')) {
    $p = Join-Path $Local $velho
    if (Test-Path $p) { Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue }
}

# --- 3. Configuração ---------------------------------------------------------------------
Etapa 'Configuração'
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$cfg = [ordered]@{}
if (Test-Path $ConfigPath) {
    try {
        $obj = Get-Content -Path $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $obj.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
    } catch { Aviso 'config.json anterior ilegível; será recriado.' }
}
$cfg['pythonPath'] = $py
$cfg['engineDirectory'] = $EngineDir
$cfg['modelsDirectory'] = $ModelsDir
$json = $cfg | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText($ConfigPath, $json, (New-Object Text.UTF8Encoding($false)))
Ok "Configuração em $ConfigPath"

# --- 4. Conferência rápida ---------------------------------------------------------------
if (-not $SemTeste) {
    Etapa 'Conferindo o motor'
    # Sem aspas duplas no código Python: o Windows PowerShell 5.1 as remove ao
    # passar argumentos para programas.
    $check = 'import forge3d, numpy, scipy, shapely, trimesh, manifold3d, skimage; print(''motor ok'', forge3d.__version__)'
    # Mensagens do Python no stderr não podem interromper o script (no 5.1, com
    # "Stop", qualquer linha no stderr vira erro fatal).
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $saida = & $py -c $check 2>&1 | ForEach-Object { "$_" }
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $anterior
    if ($codigo -ne 0) { Falha "O motor não abriu: $($saida -join ' | ')" }
    Ok ($saida | Select-Object -Last 1)
}

$segundos = [math]::Round($relogio.Elapsed.TotalSeconds, 1)
Write-Host ""
Write-Host "Forge 3D instalado em $segundos s." -ForegroundColor Green
Write-Host "Abra o Revit 2027: a aba 'Forge 3D' já estará pronta para uso."
Write-Host "Opcional: InstalarIA.cmd adiciona a reconstrução 3D por IA a partir de fotos."
exit 0
