"""FastAPI dependencies for WP1.2 (docs/adr/0010-career-profile-truth-store.md). Reuses WP1.1's
verified-token trust boundary (`tapjob_api.identity.dependencies.get_current_user`) and generic
rate limiter wholesale -- no second current-user mechanism, no second rate-limit framework
(docs/ENGINEERING_STANDARDS.md §1).
"""

from tapjob_api.core.rate_limit import RateLimitPolicy

# Named, reusable policies (docs/ENGINEERING_STANDARDS.md §2), keyed by the internal `User` UUID
# (never a raw token) exactly like WP1.1's own policies. Reads (GET) are deliberately unthrottled
# here -- only write churn is rate-limited (docs/adr/0010-...md "rate limit").
PROFILE_MUTATION_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:me:career-profile", limit=30, window_seconds=60
)
PROFILE_FACT_MUTATION_RATE_LIMIT = RateLimitPolicy(
    key_prefix="rl:me:profile-facts", limit=60, window_seconds=60
)
