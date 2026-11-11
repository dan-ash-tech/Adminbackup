#Requires -Version 4.0
<#
.SYNOPSIS
    Deployment for the Target Records app on a Windows LAN server.

.DESCRIPTION
    Safe to run repeatedly: reuses an existing .env, creates the PostgreSQL
    role/database only if missing, applies migrations and collects static files.
    Requires PostgreSQL and Python 3.12+.

    If blocked by execution policy, run once in an admin PowerShell:
        Set-ExecutionPolicy -Scope LocalMachine RemoteSigned

    Or bypass for a single run:
        powershell -ExecutionPolicy Bypass -File deploy.ps1 -Hosts "192.168.1.20" -Serve

.EXAMPLE
    # First deployment:
    .\deploy.ps1 -Hosts "192.168.1.20" -Serve

.EXAMPLE
    # Later updates (after pulling new code):
    .\deploy.ps1 -Serve
#>
[CmdletBinding()]
param(
    [string]$Hosts       = "",
    [string]$DbName      = "target_records",
    [string]$DbUser      = "target_records_app",
    [string]$PgHost      = "127.0.0.1",
    [string]$PgPort      = "5432",
    [string]$PgAdminUser = "postgres",
    [switch]$SkipDbSetup,
    [string]$Venv        = ".venv",
    [switch]$Serve,
    [string]$Port        = "8000",
    [int]   $Threads     = 8
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AppDir  = $PSScriptRoot
Set-Location $AppDir
$EnvFile = Join-Path $AppDir ".env"
$VenvDir = if ([System.IO.Path]::IsPathRooted($Venv)) { $Venv } else { Join-Path $AppDir $Venv }

# --------------------------------------------------------------------- helpers
function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn([string]$msg) { Write-Warning $msg }
function Die([string]$msg)  { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function EnvGet([string]$key) {
    if (-not (Test-Path $EnvFile)) { return "" }
    $val = ""
    foreach ($line in @(Get-Content $EnvFile -Encoding utf8)) {
        if ($line -match "^$([regex]::Escape($key))=(.*)$") { $val = $matches[1] }
    }
    $val = $val -replace '^"(.*)"$', '$1'
    $val = $val -replace "^'(.*)'$", '$1'
    return $val
}

function EnvSet([string]$key, [string]$value) {
    $lines   = if (Test-Path $EnvFile) { @(Get-Content $EnvFile -Encoding utf8) } else { @() }
    $escaped = [regex]::Escape($key)
    $found   = $false
    $result  = @()
    foreach ($line in $lines) {
        if ($line -match "^$escaped=") { $result += "$key=$value"; $found = $true }
        else { $result += $line }
    }
    if (-not $found) { $result += "$key=$value" }
    Set-Content $EnvFile -Value $result -Encoding utf8
}

function RandomToken([int]$length) {
    $tok = & $script:SystemPython -c "import secrets; print(secrets.token_urlsafe($length))"
    if ($LASTEXITCODE -ne 0) { Die "Failed to generate random token." }
    return ($tok -join "").Trim()
}

function RunPsql([string]$database, [string]$sql, [hashtable]$vars) {
    $tmp = [System.IO.Path]::GetTempFileName() + ".sql"
    try {
        Set-Content $tmp -Value $sql -Encoding ascii
        $psqlArgs = @("-X", "-q", "-v", "ON_ERROR_STOP=1",
            "-h", $script:PgHost, "-p", $script:PgPort, "-U", $script:PgAdminUser,
            "-d", $database, "-f", $tmp)
        foreach ($pair in $vars.GetEnumerator()) {
            $psqlArgs += "-v"
            $psqlArgs += "$($pair.Key)=$($pair.Value)"
        }
        & psql @psqlArgs
        if ($LASTEXITCODE -ne 0) { Die "psql command against '$database' failed (exit $LASTEXITCODE)." }
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

function Manage {
    & $script:VenvPython manage.py @args
    if ($LASTEXITCODE -ne 0) { Die "manage.py $($args[0]) failed (exit $LASTEXITCODE)." }
}

# --------------------------------------------------------------- prerequisites
Step "Checking prerequisites"

$SystemPython = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        & $candidate -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $SystemPython = $candidate; break }
    } catch {}
}
if (-not $SystemPython) { Die "Python 3.12 or newer is required." }
Write-Host "Python: $(& $SystemPython --version)"

if (-not $SkipDbSetup -and -not (Get-Command psql -ErrorAction SilentlyContinue)) {
    Die "psql was not found. Add PostgreSQL's bin folder to PATH, or pass -SkipDbSetup if the database already exists."
}

# ------------------------------------------------- python virtual environment
Step "Preparing virtual environment in $VenvDir"
if (-not (Test-Path $VenvDir)) {
    & $SystemPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Die "Failed to create virtual environment." }
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvBin    = Join-Path $VenvDir "Scripts"
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Die "pip install failed." }

# ----------------------------------------------------------------------- .env
Step "Configuring $EnvFile"
if (-not (Test-Path $EnvFile)) {
    if (-not $Hosts) { Die "-Hosts is required when creating .env (e.g. -Hosts '192.168.1.20')." }
    $csrfOrigins = ($Hosts.Split(',') | ForEach-Object { "http://$($_.Trim()):$Port" }) -join ","
    $secretKey   = RandomToken 50
    $dbPassword  = RandomToken 32
    $timestamp   = Get-Date -Format "yyyy-MM-dd HH:mm"
    $content = @"
# Generated by deploy.ps1 on $timestamp. Keep this file private.
DJANGO_SECRET_KEY=$secretKey
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=$Hosts
DJANGO_CSRF_TRUSTED_ORIGINS=$csrfOrigins

DJANGO_SECURE_CONTENT_TYPE_NOSNIFF=true
DJANGO_X_FRAME_OPTIONS=DENY

POSTGRES_DB=$DbName
POSTGRES_USER=$DbUser
POSTGRES_PASSWORD=$dbPassword
POSTGRES_HOST=$PgHost
POSTGRES_PORT=$PgPort
"@
    Set-Content $EnvFile -Value $content -Encoding utf8
    # Restrict permissions to current user (best-effort)
    try {
        $acl = Get-Acl $EnvFile
        $acl.SetAccessRuleProtection($true, $false)
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $identity, "FullControl", "Allow")
        $acl.SetAccessRule($rule)
        Set-Acl -Path $EnvFile -AclObject $acl
    } catch { Warn "Could not restrict .env permissions: $_" }
    Write-Host "Created .env with a new secret key and database password."
} else {
    Write-Host "Reusing existing .env."
    if (-not (EnvGet "POSTGRES_PASSWORD")) {
        $backup = "$EnvFile.bak.$(Get-Date -Format 'yyyyMMddHHmmss')"
        Copy-Item $EnvFile $backup
        Write-Host "Adding PostgreSQL credentials to .env (previous version saved as $(Split-Path $backup -Leaf))."
        if (-not (EnvGet "POSTGRES_DB"))   { EnvSet "POSTGRES_DB"   $DbName }
        if (-not (EnvGet "POSTGRES_USER")) { EnvSet "POSTGRES_USER" $DbUser }
        EnvSet "POSTGRES_PASSWORD" (RandomToken 32)
        if (-not (EnvGet "POSTGRES_HOST")) { EnvSet "POSTGRES_HOST" $PgHost }
        if (-not (EnvGet "POSTGRES_PORT")) { EnvSet "POSTGRES_PORT" $PgPort }
    }
    if ($Hosts) { EnvSet "DJANGO_ALLOWED_HOSTS" $Hosts }
    if ((EnvGet "DJANGO_DEBUG") -eq "true") {
        Warn "DJANGO_DEBUG=true in .env. Set it to false on any machine others can reach."
    }
}

# Re-read from .env as the source of truth
$DbName     = EnvGet "POSTGRES_DB"
$DbUser     = EnvGet "POSTGRES_USER"
$DbPassword = EnvGet "POSTGRES_PASSWORD"
$PgHost     = EnvGet "POSTGRES_HOST"
$PgPort     = EnvGet "POSTGRES_PORT"

# ------------------------------------------------------------------ database
if (-not $SkipDbSetup) {
    Step "Creating PostgreSQL role '$DbUser' and database '$DbName' if missing"
    if (-not $env:PGPASSWORD) {
        $pgSecure = Read-Host -AsSecureString "Password for PostgreSQL user '$PgAdminUser'"
        $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($pgSecure)
        $env:PGPASSWORD = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }

    $sql1 = @'
SET log_statement = 'none';
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')\gexec
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
    :'app_user', :'app_password')\gexec
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db')\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'app_db')\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'app_db', :'app_user')\gexec
'@
    RunPsql "postgres" $sql1 @{
        app_db       = $DbName
        app_user     = $DbUser
        app_password = $DbPassword
    }

    $sql2 = @'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT ALL ON SCHEMA public TO %I', :'app_user')\gexec
'@
    RunPsql $DbName $sql2 @{ app_user = $DbUser }

    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

Step "Checking the application can reach PostgreSQL"
Manage shell -c "from django.db import connection; connection.ensure_connection(); print('Connected to', connection.settings_dict['NAME'])"

Step "Applying database migrations"
Manage migrate --noinput

Step "Collecting static files"
& $VenvPython manage.py collectstatic --noinput | Out-Null
if ($LASTEXITCODE -ne 0) { Die "collectstatic failed." }

$null = New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "media")

Step "Running Django deployment checks"
Manage check --deploy --fail-level ERROR

$hasSuperuser = (& $VenvPython manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True, is_active=True).exists())" | Select-Object -Last 1).Trim()
if ($hasSuperuser -ne "True") {
    if ([Environment]::UserInteractive) {
        Step "No administrator account exists yet; creating one"
        Manage createsuperuser
    } else {
        Warn "No administrator exists. Run: $VenvPython manage.py createsuperuser"
    }
}

Write-Host ""
Write-Host "Deployment ready."
$waitressExe = Join-Path $VenvBin "waitress-serve.exe"
if ($Serve) {
    Step "Starting Waitress on 0.0.0.0:$Port (Ctrl+C to stop)"
    & $waitressExe "--listen=0.0.0.0:$Port" "--threads=$Threads" "config.wsgi:application"
} else {
    Write-Host "Start the app with:"
    Write-Host "  $waitressExe --listen=0.0.0.0:$Port --threads=$Threads config.wsgi:application"
}
