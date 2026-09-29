param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')]
    [string]$DeviceId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')]
    [string]$UserId,
    [string]$ApiBaseUrl = 'http://localhost:8000'
)
$ErrorActionPreference = 'Stop'
$dashboardPython = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
$env:HAIRSENSE_REAL_DEVICE_ID = $DeviceId
$env:HAIRSENSE_REAL_USER_ID = $UserId
$env:API_BASE_URL = $ApiBaseUrl
& $dashboardPython -m streamlit run (Join-Path $PSScriptRoot 'app.py')
if ($LASTEXITCODE -ne 0) { throw "Dashboard exited with code $LASTEXITCODE" }
