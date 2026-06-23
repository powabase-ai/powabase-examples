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

from rankforge_backend.auth import get_current_user  # noqa: E402
from rankforge_backend.models.profile import CurrentUser  # noqa: E402

# A default authenticated admin for hermetic route tests. Endpoints are gated
# behind `get_current_user`; override it so tests don't need a real GoTrue token.
# Every brand/content fixture lives in ADMIN_ORG so `assert_brand_access` (which
# loads the brand's org_id and compares it to the caller's) passes.
ADMIN_ORG = "00000000-0000-0000-0000-0000000000a0"
ADMIN_USER = CurrentUser(
    id="00000000-0000-0000-0000-000000000001",
    email="admin@test",
    role="admin",
    org_id=ADMIN_ORG,
)


def with_auth(app, user: CurrentUser = ADMIN_USER):
    """Override the auth dependency so a hermetic client is authenticated."""
    app.dependency_overrides[get_current_user] = lambda: user
    return app
