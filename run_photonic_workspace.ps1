param(
    [ValidateSet("assembly", "register")]
    [string]$StartPage = "assembly",
    [string]$Python
)

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
. (Join-Path $ProjectRoot "scripts\resolve_python.ps1")
$PythonExecutable = Resolve-ProjectPython -ExplicitPath $Python
$Registry = Join-Path $ProjectRoot "device_registry\catalog.json"
$OutputRoot = Join-Path $ProjectRoot "outputs\web_workspace"

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonExecutable -m photonic_cluster_generator.cli workspace `
    --registry $Registry `
    --output-root $OutputRoot `
    --host 127.0.0.1 `
    --port 8765 `
    --start-page $StartPage
if ($LASTEXITCODE -ne 0) {
    throw "Photonic workspace exited with code $LASTEXITCODE"
}
