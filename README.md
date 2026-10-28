# Target Records

A Django application for submitting and viewing target records. Authenticated users can view current and stopped targets; authorized staff can add and edit records. A target returns to current monitoring when its reprovision date is on or after its latest termination date.

The current and stopped lists are status-filtered views of the same target records, so setting a termination date moves a target to the stopped list and reprovisioning it moves it back without duplicating or losing its record. SQLite continues to use the existing `db.sqlite3` file so existing project data is preserved. S/N is generated automatically from the record ID and targets are listed in that order. Target records include S/N, call-sign, one or more labeled phone numbers, authorizer, case type, assigned user, service dates, image, optional Word reports, and remarks for documenting why a target was stopped. Remarks appear in the stopped-target list and can be edited with the target record. Add multiple reports with **Add another report** when submitting a target. Authorized users can download reports from the dashboard; report preview and conversion features are not provided. At the bottom of the dashboard, choose current targets, stopped targets, or both before exporting to Excel. The export contains only records the signed-in user is authorized to view and is independent of the dashboard's selected status tab and search. Targets with multiple phone numbers have one spreadsheet row per number; target details appear only on the first number's row, and targets without phone numbers still receive one row.

The dashboard shows 25 targets per page and preserves the selected status and search when moving between pages. Tabler Core 1.6.1 is bundled locally at `Adminbackup/static/case_portal/vendor/tabler.min.css`, so the interface does not depend on a runtime CDN connection. Its MIT license is included alongside the stylesheet.

## Local setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
# Put the generated key in .env; for local development set:
# DJANGO_DEBUG=true
# DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
# DJANGO_SECURE_SSL_REDIRECT=false
# DJANGO_SESSION_COOKIE_SECURE=false
# DJANGO_CSRF_COOKIE_SECURE=false
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/admin/` to manage accounts and records. The client dashboard is at `http://127.0.0.1:8000/dashboard/` and is also available at `/account/`; login is at `http://127.0.0.1:8000/accounts/login/`.

The superuser can create one user account at a time at `/users/new/`, choosing read-only access to assigned targets, read-only access to all targets, team-leader access, or target-editor permissions. To create the team leader account, use the username `team_leader` and assign the **Team leader** permission; set the password in that form. The view-all role can view every record and its files/reports but cannot change records. Team leaders can view every target and its files/reports and update existing phone-number values only; they cannot add or remove numbers or edit any other target details. Target editors receive target and phone-number add, view, change, and delete permissions; they do not receive user-management or superuser access. Use **Manage users** in the dashboard navigation (or `/users/`) to view accounts, edit user details and access level, reset passwords, or delete non-admin accounts. Superuser accounts are listed but protected from these actions. Per-user dashboard headings and statements are configured in `USER_DASHBOARD_TEXT` in `Adminbackup/views.py`, keyed by username; edit each user's entry directly in that mapping. Entries for the existing users `passy`, `user1`, `user2`, and `user3` are provided. Add another entry when you create a new account. Users not listed there see the default dashboard text. Superusers can assign users when creating a target or from the record in Django admin; only superusers can change this assignment. Each target can be assigned to at most one user. Superusers and view-all users, including team leaders, see all targets on their dashboard. Assigned users see only targets assigned to them. Viewers can access authorized targets' uploaded images and reports.

## Configuration and data safety

The app loads `.env` from the project root for local development; environment variables supplied by the deployment platform take precedence. For local development, copy `.env.example` to `.env`, generate a unique `DJANGO_SECRET_KEY` using the command above, and set the documented local values. The app refuses to start if the secret is missing or still the example placeholder; no secret key is embedded in source code. Production fails closed: `DEBUG` defaults to `false` and `DJANGO_ALLOWED_HOSTS` must contain the actual production hostnames. HTTPS redirect and secure session/CSRF cookies default to enabled when `DEBUG=false`; configure a valid TLS certificate at the hosting platform or web server, because Django does not provision certificates. Set `DJANGO_CSRF_TRUSTED_ORIGINS` to the deployment's HTTPS origins when needed. Run `python manage.py check --deploy` against the production environment before launch. Leave HSTS disabled until HTTPS has been verified, since browsers may remember the policy. Keep `.env`, database credentials, `db.sqlite3`, and `media/` out of source control. This project currently uses SQLite, which has no database password; if switching to a password-authenticated database, source its credentials from the deployment's secret environment/configuration, not from source code. The media directory may contain personal identification images and attached client documents; keep it in protected storage and restrict access. Uploaded files are not served from public `/media/` URLs; use the authenticated dashboard links, which apply per-record permissions.

The development server is for local testing only, not public hosting.
