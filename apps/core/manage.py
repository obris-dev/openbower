#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

from dotenv import load_dotenv

from openbower_kernel.environments import current_env


def main():
    load_dotenv()
    # DJANGO_ENV is always explicit (unset refuses to start); the env file
    # sets local for this machine. See .env.example.
    env = current_env()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"conf.settings.{env}")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
