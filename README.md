# Target Records

A Django application for recording and monitoring target records on a local network. All app code lives in `Adminbackup/`; project settings are in `config/`.

## Features

- **Targets** have an automatic S/N, call-sign, labeled phone numbers, authorizer, case type, assigned users, an image, Word reports (`.doc`/`.docx`), remarks, and a monitoring history. A target is *current* while its latest monitoring period is open, and *stopped* once that period has a termination date. S/N values are renumbered when a target is deleted.
- **Dashboard** with current/stopped tabs, search, 25 targets per page, and an Excel export of the records the signed-in user may see, with columns of their choice.
- **Activity log** (superusers only) of target and user changes.
- Tabler Core 1.6.1 is bundled at `Adminbackup/static/case_portal/vendor/`, so no CDN is needed.

## Roles

| Role | Sees | Can change |
|---|---|---|
| Superuser | All targets | Everything, including users and assignments |
| View all | All targets and their files | Nothing |
| Target editor | Only targets assigned to them | Add, edit and delete those targets and their phone numbers |
| Read-only viewer | Only targets assigned to them | Nothing |

These rules are enforced in one place (`CaseRecord.objects.visible_to(user)`) and apply to the dashboard, target pages, file downloads, deletes, the Excel export and `/admin/`. Only superusers assign targets. A target editor who adds a target cannot see it until a superuser assigns it to them.

Superusers manage accounts at **Manage users** (`/users/`). Accounts are **deactivated, never deleted**, so their activity history is kept. Reactivate an account from **Edit** by checking *Active*.

Per-user dashboard headings live in `USER_DASHBOARD_TEXT` in `Adminbackup/views.py`, keyed by lowercase username.

## Security

- Logins lock for 15 minutes after 5 failed attempts per username and IP address. This covers both the portal and `/admin/`. Superusers can clear a lockout early with `python manage.py axes_reset`.
- Sessions end when the browser closes or after 8 hours of inactivity.
- Uploads are limited to 10 MB. Files are removed from disk when their target or report is deleted or replaced.
- Uploaded files are never served from public URLs. Every download checks the record access rules.
- These limits can be changed in `.env` (see `.env.example`).

## Deployment (PostgreSQL)

`deploy.sh` sets up or updates a server and can be run repeatedly. It works on Linux and on Windows in Git Bash. Install Python 3.12+ and PostgreSQL first, and make sure `psql` is on the `PATH`.

```bash
# First deployment, moving the existing SQLite data into PostgreSQL:
./deploy.sh --hosts 192.168.1.20 --import-sqlite db.sqlite3 --serve

# Updates after pulling new code:
./deploy.sh --serve
```

On first run the script:

1. Creates `.venv` and installs `requirements.txt`.
2. Generates `.env` with a random secret key and database password.
3. Creates a PostgreSQL role and database with limited privileges. It asks for the `postgres` password, or use `--pg-admin-sudo` on Linux.
4. Runs migrations and, with `--import-sqlite`, copies all data across. The import refuses to run if the target database already has data, never modifies the SQLite file, and checks row counts afterwards.
5. Collects static files and runs `check --deploy`.
6. Prompts for an administrator account if none exists.

`--serve` then starts the app with Waitress on port 8000. Run `./deploy.sh --help` for all options.

**Copy `media/` to the server as well.** Uploaded images and reports are files on disk, not database rows. To keep Waitress running after logout or a reboot, register it as a service: systemd on Linux, or NSSM or Task Scheduler on Windows.

PostgreSQL should only accept connections from the app server. Keep `listen_addresses = 'localhost'` in `postgresql.conf` unless the database runs on a separate machine.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
# Put the key in .env, then for local development set:
# DJANGO_DEBUG=true
# DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
# DJANGO_DB_ENGINE=sqlite
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Run the tests with `python manage.py test Adminbackup.tests`.

## Data safety

- Keep `.env`, database credentials, `db.sqlite3` and `media/` out of source control (already in `.gitignore`).
- `media/` holds identification images and reports, so restrict who can read it on the server.
- Back up both the PostgreSQL database (`pg_dump`) and `media/`. A database backup alone does not include the files.
- Traffic is plain HTTP unless HTTPS is set up. On a shared network, put a TLS proxy (for example Caddy or nginx with an internal certificate) in front of Waitress and run `deploy.sh` with `--https`.
