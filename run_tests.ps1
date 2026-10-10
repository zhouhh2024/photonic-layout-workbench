param([string]$Python)

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
. (Join-Path $ProjectRoot "scripts\resolve_python.ps1")
$PythonExecutable = Resolve-ProjectPython -ExplicitPath $Python

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
Push-Location $ProjectRoot
try {
    & $PythonExecutable -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Test suite failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
