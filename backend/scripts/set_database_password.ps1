# Sets the PostgreSQL password inside DATABASE_URL in backend\.env without echoing it.
# Usage (PowerShell, from the backend folder):  .\scripts\set_database_password.ps1
param([string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"), [string]$User = "medaudit")

if (-not (Test-Path $EnvFile)) { Write-Error "Not found: $EnvFile"; exit 1 }
$secure = Read-Host -AsSecureString "PostgreSQL password for user '$User'"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if ([string]::IsNullOrEmpty($plain)) { Write-Error "Empty password; nothing changed."; exit 1 }
$encoded = [Uri]::EscapeDataString($plain)  # handles @ : / # and other special characters

$lines = Get-Content -LiteralPath $EnvFile -Encoding UTF8
$found = $false
$out = foreach ($line in $lines) {
  if ($line -match '^DATABASE_URL=') {
    $found = $true
    $line -replace "(postgresql\+psycopg://$([regex]::Escape($User)):)[^@]*(@)", ('${1}' + $encoded.Replace('$', '$$') + '${2}')
  } else { $line }
}
if (-not $found) { Write-Error "No DATABASE_URL line in $EnvFile"; exit 1 }
[IO.File]::WriteAllLines((Resolve-Path $EnvFile), [string[]]$out, (New-Object Text.UTF8Encoding($false)))
$plain = $null; $encoded = $null
Write-Host "DATABASE_URL updated in $EnvFile (password not displayed)."
