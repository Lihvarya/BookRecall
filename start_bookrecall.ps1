param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$BuildFrontend,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[BookRecall] $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[BookRecall] $Message" -ForegroundColor Yellow
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$FrontendDir = Join-Path $Root "frontend"
$FrontendDist = Join-Path $FrontendDir "dist\index.html"
$NodeModules = Join-Path $FrontendDir "node_modules"

# Keep model files and Hugging Face caches inside the project when possible.
$ProjectCache = Join-Path $Root ".cache"
$HuggingFaceCache = Join-Path $ProjectCache "huggingface"
$SentenceTransformersCache = Join-Path $HuggingFaceCache "sentence-transformers"
$TorchCache = Join-Path $ProjectCache "torch"
$LocalModelDir = Join-Path $Root "models"
if (-not $env:HF_HOME) {
    $env:HF_HOME = $HuggingFaceCache
}
if (-not $env:SENTENCE_TRANSFORMERS_HOME) {
    $env:SENTENCE_TRANSFORMERS_HOME = $SentenceTransformersCache
}
if (-not $env:TORCH_HOME) {
    $env:TORCH_HOME = $TorchCache
}
if (-not $env:BOOKRECALL_MODEL_DIR) {
    $env:BOOKRECALL_MODEL_DIR = $LocalModelDir
}
if (-not $env:HF_HUB_DISABLE_SYMLINKS_WARNING) {
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
}
if (-not $env:HF_HUB_DISABLE_XET) {
    $env:HF_HUB_DISABLE_XET = "1"
}

Write-Step "Project root: $Root"
Write-Step "Python: $Python"
Write-Step "Local models: $LocalModelDir"
Write-Step "Model cache: $SentenceTransformersCache"

$ShouldBuildFrontend = $false
if ($BuildFrontend) {
    $ShouldBuildFrontend = $true
} elseif ((-not $SkipFrontendBuild) -and (Test-Path $FrontendDir) -and (Test-Path $NodeModules) -and (-not (Test-Path $FrontendDist))) {
    $ShouldBuildFrontend = $true
}

if ($ShouldBuildFrontend) {
    if (-not (Test-Path $NodeModules)) {
        Write-Warn "frontend\node_modules is missing. Skipping frontend build; run npm install manually if needed."
    } else {
        Write-Step "Building Vue frontend..."
        Push-Location $FrontendDir
        try {
            npm run build
        } finally {
            Pop-Location
        }
    }
} elseif (Test-Path $FrontendDist) {
    Write-Step "Using existing Vue frontend build."
} else {
    Write-Warn "Vue build not found. The server will fall back to legacy static assets."
}

$Url = "http://${HostName}:${Port}"
if (-not $NoBrowser) {
    Write-Step "Opening browser: $Url"
    Start-Process $Url
}

Write-Step "Starting BookRecall Web. Press Ctrl+C to stop."
& $Python bookrecall.py serve --host $HostName --port $Port
