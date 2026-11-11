#!/usr/bin/env bash
# NOTE: On Windows Server use deploy.ps1 instead (native PowerShell, no Git Bash needed).
# Usage: ./deploy.sh --hosts 192.168.1.20 --serve
# Reusable deployment for the Target Records app on a Windows LAN server.
# Requires PostgreSQL. Run in Git Bash on Windows Server.
#
# Safe to run repeatedly: it reuses an existing .env, creates the PostgreSQL
# role/database only if missing, applies migrations and collects static files.
#
# First deployment:
#   ./deploy.sh --hosts 192.168.1.20 --serve
#
# Later updates (after pulling new code):
#   ./deploy.sh --serve
#
# Run ./deploy.sh --help for every option.

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# ---------------------------------------------------------------- defaults --
ENV_FILE="$APP_DIR/.env"
VENV_DIR="$APP_DIR/.venv"
HOSTS=""
DB_NAME="target_records"
DB_USER="target_records_app"
PG_HOST="127.0.0.1"
PG_PORT="5432"
PG_ADMIN_USER="postgres"
PG_ADMIN_SUDO=false
SKIP_DB_SETUP=false
SERVE=false
LISTEN_PORT="8000"
THREADS="8"

usage() {
	cat <<'EOF'
Usage: ./deploy.sh [options]

Configuration (used when .env is created; an existing .env is reused):
  --hosts LIST          Comma-separated hostnames/IPs users will type in the
                        browser, e.g. "192.168.1.20,records.lan". Required
                        the first time.
  --db-name NAME        PostgreSQL database name     (default: target_records)
  --db-user NAME        PostgreSQL application role  (default: target_records_app)
  --pg-host HOST        PostgreSQL host              (default: 127.0.0.1)
  --pg-port PORT        PostgreSQL port              (default: 5432)

Database setup:
  --pg-admin-user NAME  PostgreSQL superuser used to create the role and
                        database (default: postgres). Its password is read
                        from PGPASSWORD or prompted for.
  --skip-db-setup       The role and database already exist.

Runtime:
  --venv DIR            Python virtual environment   (default: .venv)
  --serve               Start the app with Waitress when setup finishes.
  --port PORT           Port Waitress listens on     (default: 8000)
  --threads N           Waitress worker threads      (default: 8)
  -h, --help            Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--hosts) HOSTS="$2"; shift 2 ;;
		--db-name) DB_NAME="$2"; shift 2 ;;
		--db-user) DB_USER="$2"; shift 2 ;;
		--pg-host) PG_HOST="$2"; shift 2 ;;
		--pg-port) PG_PORT="$2"; shift 2 ;;
		--pg-admin-user) PG_ADMIN_USER="$2"; shift 2 ;;
		--pg-admin-sudo) PG_ADMIN_SUDO=true; shift ;;
		--skip-db-setup) SKIP_DB_SETUP=true; shift ;;
		--venv) VENV_DIR="$2"; shift 2 ;;
		--serve) SERVE=true; shift ;;
		--port) LISTEN_PORT="$2"; shift 2 ;;
		--threads) THREADS="$2"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done

# ----------------------------------------------------------------- helpers --
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Read KEY from .env (last occurrence wins, surrounding quotes stripped).
env_get() {
	[[ -f "$ENV_FILE" ]] || return 0
	grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- \
		| sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/" || true
}

# Set KEY=VALUE in .env, replacing an existing line or appending a new one.
env_set() {
	local key="$1" value="$2" tmp
	if grep -qE "^$key=" "$ENV_FILE"; then
		tmp="$(mktemp)"
		awk -v k="$key" -v v="$value" \
			'BEGIN { FS = OFS = "=" } $1 == k { print k "=" v; next } { print }' \
			"$ENV_FILE" > "$tmp"
		cat "$tmp" > "$ENV_FILE"
		rm -f "$tmp"
	else
		printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
	fi
}

random_token() {
	"$SYSTEM_PYTHON" -c "import secrets; print(secrets.token_urlsafe($1))"
}

manage() { "$VENV_PYTHON" manage.py "$@"; }

# ---------------------------------------------------------- prerequisites --
step "Checking prerequisites"
SYSTEM_PYTHON=""
for candidate in python3 python py; do
	if command -v "$candidate" >/dev/null 2>&1 \
		&& "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
		SYSTEM_PYTHON="$candidate"
		break
	fi
done
[[ -n "$SYSTEM_PYTHON" ]] || die "Python 3.12 or newer is required."
echo "Python: $("$SYSTEM_PYTHON" --version)"

if [[ "$SKIP_DB_SETUP" == false ]] && ! command -v psql >/dev/null 2>&1; then
	die "psql was not found. Install PostgreSQL (and add its bin folder to PATH), or pass --skip-db-setup if the database already exists."
fi

# ------------------------------------------------------- python environment --
step "Preparing virtual environment in $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
	"$SYSTEM_PYTHON" -m venv "$VENV_DIR"
fi
if [[ -x "$VENV_DIR/bin/python" ]]; then
	VENV_PYTHON="$VENV_DIR/bin/python"
	VENV_BIN="$VENV_DIR/bin"
else
	VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
	VENV_BIN="$VENV_DIR/Scripts"
fi
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet -r requirements.txt

# --------------------------------------------------------------------- .env --
step "Configuring $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
	[[ -n "$HOSTS" ]] || die "--hosts is required when creating .env (e.g. --hosts 192.168.1.20)."
	csrf_origins="$(echo "$HOSTS" | tr ',' '\n' | sed "s/^ *//; s/ *\$//; s|^|http://|; s/\$/:$LISTEN_PORT/" | paste -sd, -)"
	(
		umask 077
		cat > "$ENV_FILE" <<EOF
# Generated by deploy.sh on $(date '+%Y-%m-%d %H:%M'). Keep this file private.
DJANGO_SECRET_KEY=$(random_token 50)
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=$HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS=$csrf_origins

DJANGO_SECURE_CONTENT_TYPE_NOSNIFF=true
DJANGO_X_FRAME_OPTIONS=DENY

POSTGRES_DB=$DB_NAME
POSTGRES_USER=$DB_USER
POSTGRES_PASSWORD=$(random_token 32)
POSTGRES_HOST=$PG_HOST
POSTGRES_PORT=$PG_PORT
EOF
	)
	echo "Created .env with a new secret key and database password."
else
	echo "Reusing existing .env."
	if [[ -z "$(env_get POSTGRES_PASSWORD)" ]]; then
		backup="$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
		cp "$ENV_FILE" "$backup"
		echo "Adding PostgreSQL credentials to .env (previous version saved as $(basename "$backup"))."
		[[ -n "$(env_get POSTGRES_DB)" ]] || env_set POSTGRES_DB "$DB_NAME"
		[[ -n "$(env_get POSTGRES_USER)" ]] || env_set POSTGRES_USER "$DB_USER"
		env_set POSTGRES_PASSWORD "$(random_token 32)"
		[[ -n "$(env_get POSTGRES_HOST)" ]] || env_set POSTGRES_HOST "$PG_HOST"
		[[ -n "$(env_get POSTGRES_PORT)" ]] || env_set POSTGRES_PORT "$PG_PORT"
	fi
	[[ -n "$HOSTS" ]] && env_set DJANGO_ALLOWED_HOSTS "$HOSTS"
	if [[ "$(env_get DJANGO_DEBUG)" == "true" ]]; then
		warn "DJANGO_DEBUG=true in .env. Set it to false on any machine others can reach."
	fi
fi
chmod 600 "$ENV_FILE" 2>/dev/null || true

# Values the rest of the script needs, taken from .env as the source of truth.
DB_NAME="$(env_get POSTGRES_DB)"
DB_USER="$(env_get POSTGRES_USER)"
DB_PASSWORD="$(env_get POSTGRES_PASSWORD)"
PG_HOST="$(env_get POSTGRES_HOST)"
PG_PORT="$(env_get POSTGRES_PORT)"

# --------------------------------------------------------------- database --
if [[ "$SKIP_DB_SETUP" == false ]]; then
	step "Creating PostgreSQL role '$DB_USER' and database '$DB_NAME' if missing"
	if [[ "$PG_ADMIN_SUDO" == true ]]; then
		admin_psql=(sudo -u postgres psql -X -q -v ON_ERROR_STOP=1)
	else
		if [[ -z "${PGPASSWORD:-}" ]]; then
			read -rsp "Password for PostgreSQL user '$PG_ADMIN_USER': " PGPASSWORD
			echo
			export PGPASSWORD
		fi
		admin_psql=(psql -X -q -v ON_ERROR_STOP=1 -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN_USER")
	fi

	"${admin_psql[@]}" -d postgres \
		-v app_db="$DB_NAME" -v app_user="$DB_USER" -v app_password="$DB_PASSWORD" <<'SQL'
SET log_statement = 'none';
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')\gexec
-- Keep the role's password in sync with .env on every run.
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
	:'app_user', :'app_password')\gexec
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db')\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'app_db')\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'app_db', :'app_user')\gexec
SQL

	"${admin_psql[@]}" -d "$DB_NAME" -v app_user="$DB_USER" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT ALL ON SCHEMA public TO %I', :'app_user')\gexec
SQL
	unset PGPASSWORD
fi

step "Checking the application can reach PostgreSQL"
manage shell -c "from django.db import connection; connection.ensure_connection(); print('Connected to', connection.settings_dict['NAME'])"

step "Applying database migrations"
manage migrate --noinput

# ----------------------------------------------------------------- finish --
step "Collecting static files"
manage collectstatic --noinput >/dev/null

mkdir -p media
chmod 700 media 2>/dev/null || true

step "Running Django deployment checks"
manage check --deploy --fail-level ERROR

has_superuser="$(manage shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True, is_active=True).exists())" | tail -n 1)"
if [[ "$has_superuser" != "True" ]]; then
	if [[ -t 0 ]]; then
		step "No administrator account exists yet; creating one"
		manage createsuperuser
	else
		warn "No administrator exists. Run: $VENV_PYTHON manage.py createsuperuser"
	fi
fi

echo
echo "Deployment ready."
if [[ "$SERVE" == true ]]; then
	step "Starting Waitress on 0.0.0.0:$LISTEN_PORT (Ctrl+C to stop)"
	exec "$VENV_BIN/waitress-serve" --listen="0.0.0.0:$LISTEN_PORT" --threads="$THREADS" config.wsgi:application
else
	echo "Start the app with:"
	echo "  $VENV_BIN/waitress-serve --listen=0.0.0.0:$LISTEN_PORT --threads=$THREADS config.wsgi:application"
fi
