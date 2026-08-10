import os

from django.core.wsgi import get_wsgi_application
from dotenv import load_dotenv

from conf.environments import current_env

load_dotenv()

# DJANGO_ENV is always explicit (unset refuses to start with a teaching
# error). The env file sets local for this machine (see .env.example).
env = current_env()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"conf.settings.{env}")

application = get_wsgi_application()
