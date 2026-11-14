<#
.SYNOPSIS
    Instala o módulo de IA 3D do Forge 3D (opcional).

.DESCRIPTION
    Adiciona a reconstrução 3D por IA a partir de fotos (TripoSR) e a remoção
    de fundo por IA. Baixa cerca de 4 GB uma única vez (PyTorch com CUDA 12.1,
    compatível com GPUs NVIDIA a partir da série 10; sem GPU, roda no
    processador). Depois funciona offline.

    As bibliotecas ficam em %LOCALAPPDATA%\Forge3D\ia, separadas do motor:
    atualizar o plugin não apaga a IA. Rode depois do Instalar.cmd.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$env:PYTHONNOUSERSITE = '1'  # motor isolado de pacotes 'pip --user'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Etapa([string]$texto) { Write-Host ""; Write-Host "==> $texto" -ForegroundColor Cyan }
function Ok([string]$texto) { Write-Host "    $texto" -ForegroundColor Green }
function Aviso([string]$texto) { Write-Host "    AVISO: $texto" -ForegroundColor Yellow }
function Falha([string]$texto) {
    Write-Host ""
    Write-Host "ERRO: $texto" -ForegroundColor Red
    Write-Host "O módulo de IA não foi instalado. O restante do Forge 3D continua funcionando." -ForegroundColor Red
    exit 1
}
function Executar([string]$exe, [string[]]$argumentos, [string]$descricao) {
    & $exe @argumentos
    if ($LASTEXITCODE -ne 0) { Falha "$descricao (código $LASTEXITCODE)." }
}

$Local = Join-Path $env:LOCALAPPDATA 'Forge3D'
$EngineDir = Join-Path $Local 'engine'
$py = Join-Path $EngineDir 'python\python.exe'
$IaDir = Join-Path $Local 'ia\site-packages'
$ModelsDir = Join-Path $Local 'models'
$VendorDir = Join-Path $Local 'vendor'

Write-Host "Forge 3D — módulo de IA 3D (opcional)" -ForegroundColor White
if (-not (Test-Path $py)) { Falha 'Instale o Forge 3D primeiro (Instalar.cmd).' }
if (Get-Process -Name 'Revit' -ErrorAction SilentlyContinue) { Falha 'Feche o Revit antes de instalar a IA.' }

# --- 1. Bibliotecas ----------------------------------------------------------------------
Etapa 'Baixando e instalando o PyTorch e as bibliotecas de IA (vários minutos)'
New-Item -ItemType Directory -Force -Path $IaDir | Out-Null
# As versões das bibliotecas que o motor já tem ficam travadas (-c), para
# que a IA use exatamente as mesmas.
Executar $py @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-warn-script-location', '--upgrade',
    '--target', $IaDir,
    'torch==2.5.1+cu121', 'torchvision==0.20.1+cu121',
    '-r', (Join-Path $EngineDir 'requirements-ia.txt'),
    '-c', (Join-Path $EngineDir 'requirements.txt'),
    '--extra-index-url', 'https://download.pytorch.org/whl/cu121') 'Falha ao instalar as bibliotecas de IA'

# Remove da pasta da IA as bibliotecas que o motor já traz (uma cópia só).
$dedup = @"
import sys, shutil, importlib.metadata as md
from pathlib import Path
ia, base = Path(sys.argv[1]), Path(sys.argv[2])
norm = lambda n: n.lower().replace('_', '-').replace('.', '-')
base_names = {norm(d.metadata['Name']) for d in md.distributions(path=[str(base)])}
removed = 0
for d in list(md.distributions(path=[str(ia)])):
    if norm(d.metadata['Name']) not in base_names:
        continue
    for f in d.files or []:
        p = Path(d.locate_file(f))
        if p.is_file() and ia in p.parents:
            p.unlink()
    shutil.rmtree(str(d._path), ignore_errors=True)
    removed += 1
for p in sorted(ia.rglob('*'), key=lambda q: len(q.parts), reverse=True):
    if p.is_dir() and not any(p.iterdir()):
        p.rmdir()
print(f'{removed} biblioteca(s) duplicada(s) removida(s)')
"@
Executar $py @('-c', $dedup, $IaDir, (Join-Path $EngineDir 'python\Lib\site-packages')) 'Falha ao organizar as bibliotecas de IA'
Ok "Bibliotecas em $IaDir"

# --- 2. Código do TripoSR ----------------------------------------------------------------
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

# --- 3. Modelos --------------------------------------------------------------------------
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

# --- 4. Conferência ----------------------------------------------------------------------
Etapa 'Conferindo a IA'
Executar $py @('-m', 'forge3d', 'hardware') 'O motor não reconheceu o módulo de IA'

Write-Host ""
Write-Host "Módulo de IA 3D instalado. Abra o Revit e use Forge 3D > Imagem." -ForegroundColor Green
exit 0
