"""Settings used by the isolated unit-test suite.

This module deliberately avoids infrastructure owned by a developer or a
deployment environment.  Database rows, cache entries, emails, and Celery
tasks live only for the duration of the test process.
"""

import os
import sys
import types
from datetime import datetime


_LOCAL_TEST_ENDPOINT = "http://127.0.0.1:1"

# Force non-sensitive test placeholders instead of inheriting values from a
# developer shell or CI secret store.
os.environ.update(
    {
        "APP_ID": "bk_itsm_unit_test",
        "APP_TOKEN": "unit-test-placeholder",
        "BKPAAS_APP_ID": "bk_itsm_unit_test",
        "BKPAAS_APP_SECRET": "unit-test-placeholder",
        "BKPAAS_ENVIRONMENT": "dev",
        "BK_ENV": "development",
        "BK_PAAS_HOST": _LOCAL_TEST_ENDPOINT,
        "BK_PAAS_INNER_HOST": _LOCAL_TEST_ENDPOINT,
        "BK_PAAS2_URL": _LOCAL_TEST_ENDPOINT,
        "BK_COMPONENT_API_URL": _LOCAL_TEST_ENDPOINT,
        "BK_IAM_INNER_HOST": _LOCAL_TEST_ENDPOINT,
        "BK_IAM_V3_INNER_HOST": _LOCAL_TEST_ENDPOINT,
        "RUN_VER": "open",
        "LOCAL_DEV_LOGIN": "0",
        "LOCAL_DEV_USERNAME": "unit_test_user",
        "BKAPP_IAM_SKIP_AUTH": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "NO_PROXY": "127.0.0.1,localhost",
    }
)

# Database, Redis, and broker values supplied by a workstation or pipeline are
# forbidden in this unit-test process.  The in-memory replacements below are
# the only permitted backends.
for _environment_name in (
    "DATABASE_URL",
    "BK_MYSQL_NAME",
    "BK_MYSQL_USER",
    "BK_MYSQL_PASSWORD",
    "BK_MYSQL_HOST",
    "BK_MYSQL_PORT",
    "BK_MYSQL_TEST_NAME",
    "BKAPP_DB_TEST",
    "BKAPP_MYSQL_USER",
    "BKAPP_MYSQL_PASS",
    "BKAPP_MYSQL_IP",
    "BKAPP_MYSQL_PORT",
    "BKAPP_REDIS_HOST",
    "BKAPP_REDIS_PORT",
    "BKAPP_REDIS_PASSWORD",
    "BKAPP_REDIS_DB",
    "BROKER_URL",
):
    os.environ.pop(_environment_name, None)

# config.dev imports config.local_settings when that file exists.  Install an
# empty in-memory module first so unit tests never execute or read the ignored
# developer configuration file (which normally loads .env).
_isolated_local_settings = types.ModuleType("config.local_settings")
_isolated_local_settings.__all__ = []
sys.modules["config.local_settings"] = _isolated_local_settings

from settings import *  # noqa: E402,F401,F403


# Every ORM test uses an in-process database which disappears with the test
# process.  MySQL-specific behaviour must be covered by a separate integration
# suite rather than by this unit-test configuration.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}


# Keep all cache aliases expected by BK-ITSM and blueapps, but back them with
# process-local memory instead of MySQL or Redis.
CACHES = {
    alias: {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "bk-itsm-unit-test-{}".format(alias),
    }
    for alias in ("default", "db", "login_db", "debug-panel", "locmem")
}
CACHES["dummy"] = {
    "BACKEND": "django.core.cache.backends.dummy.DummyCache",
}


# common.redis reads these settings when it is imported, even when Django's
# cache backend is LocMemCache.  Direct Redis clients still need to be mocked
# by the test which exercises them.
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_PASSWORD = ""
REDIS_SENTINEL_PASSWORD = ""


# Do not enqueue jobs or send email from unit tests.
BROKER_URL = "memory://"
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_ALWAYS_EAGER = True
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


# Fast, deterministic password hashing is sufficient for test-only users.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


# Unit tests opt in to authentication explicitly.  Removing the API Gateway
# middleware also makes it possible to exercise the missing-JWT path without a
# gateway being present in the test process.
BK_APIGW_REQUIRE_EXEMPT = False
IAM_SKIP_AUTH = True
MIDDLEWARE = tuple(
    middleware
    for middleware in MIDDLEWARE
    if not middleware.startswith("apigw_manager.apigw.authentication.ApiGatewayJWT")
    and middleware != "config.local_settings.LocalDevAuthMiddleware"
)


# A few normal query paths use MySQL's FIELD(value, ...) ordering helper.
# Register its small, deterministic equivalent so those paths remain unit
# testable on SQLite.  MySQL-specific integration semantics are not asserted.
def _sqlite_field(value, *choices):
    try:
        return choices.index(value) + 1
    except ValueError:
        return 0


def _sqlite_date_format(value, format_string):
    """Small DATE_FORMAT equivalent used by operational reporting queries."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.strftime(format_string)


def _register_sqlite_functions(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        connection.connection.create_function("FIELD", -1, _sqlite_field)
        connection.connection.create_function(
            "DATE_FORMAT", 2, _sqlite_date_format
        )


from django.db.backends.signals import connection_created  # noqa: E402

connection_created.connect(
    _register_sqlite_functions,
    dispatch_uid="bk_itsm_unit_test_sqlite_functions",
)
