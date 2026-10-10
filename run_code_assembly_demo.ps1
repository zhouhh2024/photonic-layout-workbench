param([string]$Python)

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
. (Join-Path $ProjectRoot "scripts\resolve_python.ps1")
$PythonExecutable = Resolve-ProjectPython -ExplicitPath $Python

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
Push-Location $ProjectRoot
try {
    & $PythonExecutable (Join-Path $ProjectRoot "examples\build_code_assembly_demo.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Code assembly demo failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
