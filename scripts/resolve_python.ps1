function Resolve-ProjectPython {
    param([string]$ExplicitPath)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        if (Test-Path -LiteralPath $ExplicitPath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $ExplicitPath).Path
        }
        $explicitCommand = Get-Command $ExplicitPath -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $explicitCommand) {
            return $explicitCommand.Source
        }
        throw "Python interpreter not found: $ExplicitPath"
    }

    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_PREFIX)) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $condaPython -PathType Leaf) {
            return $condaPython
        }
    }

    $pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        return $pythonCommand.Source
    }

    throw "Python is unavailable. Activate the Conda environment or pass -Python <path>."
}
