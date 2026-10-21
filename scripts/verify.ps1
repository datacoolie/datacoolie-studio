param(
    [ValidateSet("Fast", "Backend", "Full", "Release")]
    [string]$Mode = "Fast"
)

$ErrorActionPreference = "Stop"
$studioRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $studioRoot
$pythonExecutable = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$verifyScript = Join-Path $studioRoot "scripts\verify.py"

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Expected workspace Python interpreter was not found: $pythonExecutable"
}

$resolvedPythonExecutable = (Resolve-Path -LiteralPath $pythonExecutable).Path
Write-Host "Using workspace Python: $resolvedPythonExecutable"

Push-Location $studioRoot
try {
    & $resolvedPythonExecutable $verifyScript $Mode.ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "Studio verification group '$Mode' failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
