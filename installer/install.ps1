<#
.SYNOPSIS
    Instala o Forge 3D para o Revit 2027.

.DESCRIPTION
    1. Copia o plugin para %APPDATA%\Autodesk\Revit\Addins\2027.
    2. Copia o motor para %LOCALAPPDATA%\Forge3D\engine.
    3. Cria um Python 3.12 isolado (com o uv) e instala as bibliotecas do motor.
    4. Opcional (-ComIA): instala o módulo de IA 3D (PyTorch com CUDA 12.1,
       TripoSR e remoção de fundo) e baixa os modelos uma única vez.
    5. Grava a configuração e testa o motor.

    Depois da instalação, tudo funciona offline.

.PARAMETER ComIA
    Instala o módulo de IA 3D (cerca de 4 GB de download).

.PARAMETER SemPerguntas
    Não faz perguntas (usa -ComIA para decidir sobre a IA).

.PARAMETER Origem
    Pasta do pacote (contém "addin" e "engine"). Padrão: pasta acima deste script.
#>
[CmdletBinding()]
param(
    [switch]$ComIA,
    [switch]$SemPerguntas,
    [string]$Origem = ""
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Etapa([string]$texto) { Write-Host ""; Write-Host "==> $texto" -ForegroundColor Cyan }
function Ok([string]$texto) { Write-Host "    $texto" -ForegroundColor Green }
function Aviso([string]$texto) { Write-Host "    AVISO: $texto" -ForegroundColor Yellow }
function Falha([string]$texto) {
    Write-Host ""
    Write-Host "ERRO: $texto" -ForegroundColor Red
    Write-Host "A instalação não foi concluída. Nada foi alterado no Revit além do que já foi copiado." -ForegroundColor Red
    exit 1
}

function Executar([string]$exe, [string[]]$argumentos, [string]$descricao) {
    & $exe @argumentos
    if ($LASTEXITCODE -ne 0) { Falha "$descricao (código $LASTEXITCODE)." }
}

if ([string]::IsNullOrWhiteSpace($Origem)) { $Origem = Split-Path -Parent $PSScriptRoot }
$Origem = (Resolve-Path $Origem).Path

$Local = Join-Path $env:LOCALAPPDATA 'Forge3D'
$EngineDir = Join-Path $Local 'engine'
$ModelsDir = Join-Path $Local 'models'
$VendorDir = Join-Path $Local 'vendor'
$ToolsDir = Join-Path $Local 'tools'
$AddinRoot = Join-Path $env:APPDATA 'Autodesk\Revit\Addins\2027'
$AddinDir = Join-Path $AddinRoot 'Forge3D'
$ConfigDir = Join-Path $env:APPDATA 'Forge3D'
$ConfigPath = Join-Path $ConfigDir 'config.json'

Write-Host "Forge 3D — instalação para o Revit 2027" -ForegroundColor White
Write-Host "Pacote: $Origem"

# --- 0. Verificações ---------------------------------------------------------------------
if (Get-Process -Name 'Revit' -ErrorAction SilentlyContinue) { Falha 'Feche o Revit antes de instalar.' }
$pkgAddin = Join-Path $Origem 'addin'
$pkgEngine = Join-Path $Origem 'engine'
if (-not (Test-Path (Join-Path $pkgAddin 'Forge3D.Revit.dll'))) { Falha "Pacote incompleto: não encontrei $pkgAddin\Forge3D.Revit.dll" }
if (-not (Test-Path (Join-Path $pkgEngine 'forge3d'))) { Falha "Pacote incompleto: não encontrei $pkgEngine\forge3d" }

if (-not $SemPerguntas -and -not $ComIA) {
    Write-Host ""
    Write-Host "O módulo de IA 3D permite gerar famílias a partir de fotos de objetos sem simetria."
    Write-Host "Ele baixa cerca de 4 GB (PyTorch e modelos) uma única vez; depois funciona offline."
    $resp = Read-Host "Instalar o módulo de IA 3D agora? (S/N)"
    if ($resp -match '^[sSyY]') { $ComIA = $true }
}

# --- 1. Plugin ---------------------------------------------------------------------------
Etapa 'Instalando o plugin do Revit 2027'
New-Item -ItemType Directory -Force -Path $AddinDir | Out-Null
Get-ChildItem -Path $pkgAddin | Where-Object { $_.Name -ne 'Forge3D.addin' } | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $AddinDir -Recurse -Force
}
$manifest = Get-Content -Path (Join-Path $pkgAddin 'Forge3D.addin') -Raw -Encoding UTF8
$dll = Join-Path $AddinDir 'Forge3D.Revit.dll'
$manifest = $manifest -replace '<Assembly>[^<]*</Assembly>', "<Assembly>$dll</Assembly>"
[IO.File]::WriteAllText((Join-Path $AddinRoot 'Forge3D.addin'), $manifest, (New-Object Text.UTF8Encoding($false)))
Ok "Plugin em $AddinDir"

# --- 2. Motor ----------------------------------------------------------------------------
Etapa 'Copiando o motor'
New-Item -ItemType Directory -Force -Path $EngineDir | Out-Null
$oldPkg = Join-Path $EngineDir 'forge3d'
if (Test-Path $oldPkg) { Remove-Item -Path $oldPkg -Recurse -Force }
Copy-Item -Path (Join-Path $pkgEngine 'forge3d') -Destination $EngineDir -Recurse -Force
foreach ($f in @('pyproject.toml', 'requirements.txt', 'requirements-ia.txt', 'README.md')) {
    $src = Join-Path $pkgEngine $f
    if (Test-Path $src) { Copy-Item -Path $src -Destination $EngineDir -Force }
}
Ok "Motor em $EngineDir"

# --- 3. Python isolado (uv) --------------------------------------------------------------
Etapa 'Preparando o Python 3.12 do motor'
New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
$uv = Join-Path $ToolsDir 'uv.exe'
if (-not (Test-Path $uv)) {
    $zip = Join-Path $env:TEMP 'forge3d-uv.zip'
    $url = 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    } catch {
        Falha "Não foi possível baixar o uv ($url). Verifique a internet: ela só é necessária durante a instalação."
    }
    $tmp = Join-Path $env:TEMP 'forge3d-uv'
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    Copy-Item -Path (Get-ChildItem -Path $tmp -Recurse -Filter 'uv.exe' | Select-Object -First 1).FullName -Destination $uv -Force
    Remove-Item $zip -Force
}
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Local 'python'
$env:UV_CACHE_DIR = Join-Path $Local 'cache'
$venv = Join-Path $EngineDir '.venv'
$py = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $py)) {
    Executar $uv @('venv', '--python', '3.12', $venv) 'Falha ao criar o ambiente Python'
}
Ok 'Python pronto'

Etapa 'Instalando as bibliotecas de geometria'
Executar $uv @('pip', 'install', '--python', $py, '-r', (Join-Path $EngineDir 'requirements.txt')) 'Falha ao instalar as bibliotecas do motor'
Ok 'Bibliotecas instaladas'

# --- 4. Módulo de IA (opcional) ----------------------------------------------------------
if ($ComIA) {
    Etapa 'Instalando o módulo de IA 3D (pode levar vários minutos)'
    Executar $uv @('pip', 'install', '--python', $py, 'torch==2.5.1', 'torchvision==0.20.1',
        '--index-url', 'https://download.pytorch.org/whl/cu121') 'Falha ao instalar o PyTorch'
    Executar $uv @('pip', 'install', '--python', $py, '-r', (Join-Path $EngineDir 'requirements-ia.txt')) 'Falha ao instalar as bibliotecas de IA'

    $tripoDir = Join-Path $VendorDir 'TripoSR'
    New-Item -ItemType Directory -Force -Path $VendorDir | Out-Null
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    if (-not (Test-Path (Join-Path $tripoDir 'tsr'))) {
        Etapa 'Baixando o código do TripoSR (licença MIT)'
        $zip = Join-Path $env:TEMP 'forge3d-triposr.zip'
        $baixou = $false
        try {
            Invoke-WebRequest -Uri 'https://codeload.github.com/VAST-AI-Research/TripoSR/zip/refs/heads/main' -OutFile $zip -UseBasicParsing
            $tmp = Join-Path $env:TEMP 'forge3d-triposr'
            if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
            Expand-Archive -Path $zip -DestinationPath $tmp -Force
            $inner = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
            if (Test-Path $tripoDir) { Remove-Item $tripoDir -Recurse -Force }
            Move-Item -Path $inner.FullName -Destination $tripoDir
            Remove-Item $zip -Force
            $baixou = $true
        } catch {
            Aviso 'GitHub indisponível; usando a cópia oficial do código no Hugging Face.'
        }
        if (-not $baixou) {
            $hf = "from huggingface_hub import snapshot_download`nsnapshot_download('stabilityai/TripoSR', repo_type='space', local_dir=r'$tripoDir', allow_patterns=['tsr/*', 'tsr/**'])"
            Executar $py @('-c', $hf) 'Falha ao baixar o código do TripoSR'
        }
    }
    Etapa 'Baixando os modelos de IA (uma única vez)'
    $env:FORGE3D_MODELS = $ModelsDir
    $env:U2NET_HOME = Join-Path $ModelsDir 'u2net'
    # Cache do Hugging Face do Forge 3D: o TripoSR lê a configuração do
    # codificador de imagem (facebook/dino-vitb16) ao carregar; com ela no
    # cache, o motor funciona sem internet.
    $env:HF_HOME = Join-Path $ModelsDir 'hf'
    $script = @"
from huggingface_hub import snapshot_download, hf_hub_download
snapshot_download('stabilityai/TripoSR', local_dir=r'$ModelsDir\TripoSR', allow_patterns=['config.yaml', 'model.ckpt'])
hf_hub_download('facebook/dino-vitb16', 'config.json')
from rembg import new_session
new_session('u2net')
print('modelos prontos')
"@
    Executar $py @('-c', $script) 'Falha ao baixar os modelos de IA'
    Ok "Modelos em $ModelsDir"
}

# --- 5. Configuração ---------------------------------------------------------------------
Etapa 'Gravando a configuração'
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$cfg = @{}
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

# --- 6. Teste ----------------------------------------------------------------------------
Etapa 'Testando o motor'
$env:PYTHONPATH = $EngineDir
$env:FORGE3D_MODELS = $ModelsDir
Push-Location $EngineDir
try {
    & $py -m forge3d hardware
    if ($LASTEXITCODE -ne 0) { Falha 'O motor não respondeu ao teste.' }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Forge 3D instalado com sucesso." -ForegroundColor Green
Write-Host "Abra o Revit 2027: a aba 'Forge 3D' aparece na faixa de opções."
Write-Host "Dica: rode 'Diagnóstico' na aba Forge 3D para testar tudo dentro do Revit."
