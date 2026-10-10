param([string]$Python)

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
. (Join-Path $ProjectRoot "scripts\resolve_python.ps1")
$PythonExecutable = Resolve-ProjectPython -ExplicitPath $Python

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonExecutable -m photonic_cluster_generator.cli generate `
    (Join-Path $ProjectRoot "examples\lvs_mzi_cluster.json") `
    --output-dir (Join-Path $ProjectRoot "outputs\lvs_mzi_cluster")
if ($LASTEXITCODE -ne 0) {
    throw "Demo generation failed with exit code $LASTEXITCODE"
}
