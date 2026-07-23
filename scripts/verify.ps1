param(
    [ValidateSet("Fast", "Full")]
    [string]$Mode = "Fast"
)

$ErrorActionPreference = "Stop"
$studioRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $studioRoot "frontend"

function Invoke-Checked {
    param(
        [Parameter(Mandatory)]
        [scriptblock]$Command,
        [Parameter(Mandatory)]
        [string]$Label
    )

    Write-Host "`n==> $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Push-Location $studioRoot
try {
    if ($Mode -eq "Full") {
        Invoke-Checked { python -m pytest } "Backend full suite"
    } else {
        Invoke-Checked {
            python -m pytest `
                tests/test_architecture_boundaries.py `
                tests/test_main.py `
                tests/test_openapi_export.py
        } "Backend fast regression"
    }

    Invoke-Checked {
        python -m datacoolie_studio.openapi_export --check
    } "OpenAPI contract"

    Push-Location $frontendRoot
    try {
        Invoke-Checked { npm run api:check } "Generated API types"
        Invoke-Checked { npm test } "Frontend suite"
        Invoke-Checked { npm run build } "Frontend production build"
        Invoke-Checked { npm audit --audit-level=high } "Frontend dependency audit"
    } finally {
        Pop-Location
    }
} finally {
    Pop-Location
}
