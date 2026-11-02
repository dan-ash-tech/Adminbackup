# Daily backup of the Target Records PostgreSQL database and uploaded media.
#
# Usage (PowerShell):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1 -Destination E:\Backups -KeepDays 60
#
# Database credentials are read from .env; the password is passed to pg_dump
# through the PGPASSWORD environment variable, never on the command line.
#
# Restore a backup into an empty database:
#   pg_restore -h 127.0.0.1 -U target_records_app -d target_records --clean --if-exists <folder>\target_records.dump
#   then unzip <folder>\media.zip over the project's media\ folder.

param(
	[string]$Destination = "C:\Users\PC\Documents\TargetRecordsBackups",
	[int]$KeepDays = 30,
	[string]$PgBin = "C:\Program Files\PostgreSQL\18\bin"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$LogFile = Join-Path $Destination "backup.log"

function Write-Log([string]$Message) {
	$line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Message
	Write-Output $line
	Add-Content -Path $LogFile -Value $line -Encoding utf8
}

function Read-DotEnv([string]$Path) {
	$values = @{}
	foreach ($line in Get-Content -Path $Path -Encoding utf8) {
		if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
			$values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
		}
	}
	return $values
}

# Backups hold sensitive personal data: only this user, SYSTEM and Administrators may read them.
if (-not (Test-Path $Destination)) {
	New-Item -ItemType Directory -Path $Destination | Out-Null
	icacls $Destination /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /Q | Out-Null
}

try {
	$envValues = Read-DotEnv (Join-Path $ProjectDir ".env")
	if ($envValues["DJANGO_DB_ENGINE"] -ne "postgresql") {
		throw "DJANGO_DB_ENGINE in .env is not 'postgresql'; nothing to back up."
	}

	$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
	$target = Join-Path $Destination $stamp
	New-Item -ItemType Directory -Path $target -Force | Out-Null
	Write-Log "Starting backup to $target"

	$dumpFile = Join-Path $target "target_records.dump"
	$env:PGPASSWORD = $envValues["POSTGRES_PASSWORD"]
	try {
		& (Join-Path $PgBin "pg_dump.exe") `
			--host=$($envValues["POSTGRES_HOST"]) `
			--port=$($envValues["POSTGRES_PORT"]) `
			--username=$($envValues["POSTGRES_USER"]) `
			--dbname=$($envValues["POSTGRES_DB"]) `
			--format=custom --no-password --file=$dumpFile
		if ($LASTEXITCODE -ne 0) { throw "pg_dump failed with exit code $LASTEXITCODE." }
	}
	finally {
		Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
	}
	Write-Log ("Database dump: {0:N0} bytes" -f (Get-Item $dumpFile).Length)

	$mediaDir = Join-Path $ProjectDir "media"
	if (Test-Path $mediaDir) {
		$mediaZip = Join-Path $target "media.zip"
		Compress-Archive -Path (Join-Path $mediaDir "*") -DestinationPath $mediaZip -CompressionLevel Optimal
		Write-Log ("Media archive: {0:N0} bytes" -f (Get-Item $mediaZip).Length)
	}

	$cutoff = (Get-Date).AddDays(-$KeepDays)
	Get-ChildItem -Path $Destination -Directory |
		Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_\d{4}$' -and $_.LastWriteTime -lt $cutoff } |
		ForEach-Object {
			Remove-Item -Path $_.FullName -Recurse -Force
			Write-Log "Removed old backup $($_.Name)"
		}

	Write-Log "Backup finished"
}
catch {
	Write-Log "BACKUP FAILED: $($_.Exception.Message)"
	exit 1
}
