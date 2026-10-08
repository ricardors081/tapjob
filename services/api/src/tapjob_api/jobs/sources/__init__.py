"""External Job Source adapters and registry (WP2.1-B).

Provides adapters conforming to `tapjob_api.ports.JobSource` for Greenhouse, Lever, and Ashby,
along with capability registry and source health monitoring.
"""

from tapjob_api.jobs.sources.ashby import AshbyJobSource
from tapjob_api.jobs.sources.greenhouse import GreenhouseJobSource
from tapjob_api.jobs.sources.lever import LeverJobSource
from tapjob_api.jobs.sources.registry import (
    JobSourceConfig,
    JobSourceRegistry,
    get_default_job_source_registry,
)

__all__ = [
    "AshbyJobSource",
    "GreenhouseJobSource",
    "JobSourceConfig",
    "JobSourceRegistry",
    "LeverJobSource",
    "get_default_job_source_registry",
]
