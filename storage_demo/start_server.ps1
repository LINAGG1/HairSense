param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')]
    [string]$DeviceId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')]
    [string]$UserId,

    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$serverPython = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $serverPython)) {
    throw "Project Python environment not found: $serverPython"
}

# Bind the child server to the explicitly supplied collection device and user.
$env:HAIRSENSE_REAL_DEVICE_ID = $DeviceId
$env:HAIRSENSE_REAL_USER_ID = $UserId
Push-Location -LiteralPath $PSScriptRoot
try {
    Write-Host "Starting real capture server: device=$DeviceId user=$UserId port=$Port"
    & $serverPython -m uvicorn app:app --host 0.0.0.0 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
