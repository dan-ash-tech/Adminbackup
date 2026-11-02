#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


# `runserver` is only for development on this PC. .env holds the live site's
# production settings (HTTPS behind Caddy), which make http://127.0.0.1:8000
# fail with "Bad Request (400)", SSL errors or CSRF 403s, so they are replaced
# for this command only. They are assigned rather than defaulted because VS Code
# (python.terminal.useEnvFile) copies .env into every terminal. The live site
# runs under Waitress via config.wsgi and never goes through this file.
DEV_SERVER_SETTINGS = {
    'DJANGO_DEBUG': 'true',
    'DJANGO_ALLOWED_HOSTS': '127.0.0.1,localhost',
    'DJANGO_SECURE_SSL_REDIRECT': 'false',
    'DJANGO_SESSION_COOKIE_SECURE': 'false',
    'DJANGO_CSRF_COOKIE_SECURE': 'false',
}


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    if len(sys.argv) > 1 and sys.argv[1] == 'runserver':
        os.environ.update(DEV_SERVER_SETTINGS)
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
