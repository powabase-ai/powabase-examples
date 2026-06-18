"""Test config — keep the suite hermetic.

backend/.env now holds real Powabase credentials. Without this, the app lifespan
would open a real DB pool / Powabase client during tests. Empty env vars take
precedence over the .env file in pydantic-settings, so the app starts with no DB
and no Powabase client — all dependencies are injected/mocked per test.
"""

import os

os.environ["POWABASE_BASE_URL"] = ""
os.environ["POWABASE_SERVICE_ROLE_KEY"] = ""
os.environ["POWABASE_DATABASE_URL"] = ""

from rankforge_backend.config import get_settings  # noqa: E402

get_settings.cache_clear()
