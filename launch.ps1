$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.python'
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot 'var\uv-cache'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $PSScriptRoot '.venv'
$env:PYTHONUTF8 = '1'
$uvPath = Join-Path $PSScriptRoot 'bin\uv.exe'
if (-not (Test-Path -LiteralPath $uvPath)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'bin'), (Join-Path $PSScriptRoot 'var') | Out-Null
    $uvArchive = Join-Path $PSScriptRoot 'var\uv-bootstrap.zip'
    $uvUrl = 'https://github.com/astral-sh/uv/releases/download/0.11.32/uv-x86_64-pc-windows-msvc.zip'
    $uvHash = 'acfde570451cfdb8689fa159a138ee805ba4e241c466432750302c86254b0984'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host 'Preparing the project-local runtime from the official uv release...'
    Invoke-WebRequest -UseBasicParsing -Uri $uvUrl -OutFile $uvArchive
    if ((Get-FileHash -LiteralPath $uvArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $uvHash) {
        throw 'uv download checksum mismatch. The downloaded executable was not run.'
    }
    Expand-Archive -LiteralPath $uvArchive -DestinationPath (Join-Path $PSScriptRoot 'bin') -Force
    if (-not (Test-Path -LiteralPath $uvPath)) { throw 'uv.exe is missing from the official runtime archive.' }
}
& $uvPath sync --frozen --no-dev --python 3.13 --managed-python
if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить виртуальную среду внутри проекта.' }
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($args.Count -eq 0) {
    & $pythonPath -m archive serve
} else {
    & $pythonPath -m archive @args
}
exit $LASTEXITCODE
