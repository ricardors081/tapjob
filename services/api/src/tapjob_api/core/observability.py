"""Sentry wiring, shared by the API and the worker (docs/FOUNDATION_DECISIONS.md "Technical
observability").

Initialization is a no-op unless ``sentry_dsn`` is configured, so CI and local development never
send an event. ``EXPO_PUBLIC_SENTRY_DSN``'s backend counterpart here is not itself a secret (DSNs
are meant to be embedded in clients), but ``SENTRY_AUTH_TOKEN`` (source-map/release upload) is a
build-time-only secret and must never be read by this module or the running application.
"""

import sentry_sdk

from tapjob_api.core.config import RuntimeSettings


def init_sentry(settings: RuntimeSettings, *, service: str) -> None:
    if not settings.sentry_dsn:
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        server_name=service,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
