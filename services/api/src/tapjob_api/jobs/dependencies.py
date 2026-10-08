"""Dependencies and rate limit policies for Jobs API (WP2.1-A)."""

from tapjob_api.core.rate_limit import RateLimitPolicy
from tapjob_api.jobs.http_client import SafeHttpClient

JOB_PASTE_INTAKE_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:jobs:intake:paste", limit=30, window_seconds=3600
)

JOB_URL_INTAKE_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:jobs:intake:url", limit=15, window_seconds=3600
)


def get_safe_http_client() -> SafeHttpClient:
    """Dependency provider for SafeHttpClient."""
    return SafeHttpClient()
