"""Connector capability registry, configuration models, and health tracking (WP2.1-B).

Maintains configured JobSource instances, enabling:
- Typed, fail-closed configuration of external hosted ATS boards.
- Centralized lookup and discovery across active ATS connectors.
- Periodic health status probing.
- Operational provenance association (source_id, company_name, corporate_domain).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from tapjob_api.jobs.sources.ashby import AshbyJobSource
from tapjob_api.jobs.sources.greenhouse import GreenhouseJobSource
from tapjob_api.jobs.sources.lever import LeverJobSource
from tapjob_api.ports import (
    AdapterHealth,
    AdapterHealthStatus,
    CallContext,
    JobSource,
)

logger = logging.getLogger(__name__)

SUPPORTED_ATS_PROVIDERS = frozenset({"greenhouse", "lever", "ashby"})


class JobSourceConfigError(ValueError):
    """Raised when ATS board configuration is invalid or incomplete."""


class HostedAtsSourceEntry(BaseModel):
    """Typed configuration entry for one hosted ATS job board."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    token: str
    company_name: str
    corporate_domain: str | None = None
    enabled: bool = True
    source_id: str | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        val = v.strip().lower()
        if val not in SUPPORTED_ATS_PROVIDERS:
            raise JobSourceConfigError(
                f"Unknown ATS provider '{v}'; must be one of: {sorted(SUPPORTED_ATS_PROVIDERS)}"
            )
        return val

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        val = v.strip()
        if not val:
            raise JobSourceConfigError("token must not be empty")
        return val

    @field_validator("company_name")
    @classmethod
    def validate_company_name(cls, v: str) -> str:
        val = v.strip()
        if not val:
            raise JobSourceConfigError("company_name must not be empty")
        return val


@dataclass(frozen=True, slots=True)
class JobSourceConfig:
    """Configuration and provenance binding for an external JobSource."""

    source_id: str
    provider: str
    source: JobSource
    company_name: str
    corporate_domain: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id must not be empty")
        if not self.provider or not self.provider.strip():
            raise ValueError("provider must not be empty")
        if not self.company_name or not self.company_name.strip():
            raise ValueError("company_name must not be empty")


def build_job_source_config(entry: HostedAtsSourceEntry) -> JobSourceConfig:
    """Instantiate adapter and construct JobSourceConfig with deterministic source_id."""
    prov = entry.provider.lower()
    source_id = (
        entry.source_id.strip()
        if (entry.source_id and entry.source_id.strip())
        else f"{prov}:{entry.token}"
    )

    adapter: JobSource
    if prov == "greenhouse":
        adapter = GreenhouseJobSource(board_token=entry.token, company_name=entry.company_name)
    elif prov == "lever":
        adapter = LeverJobSource(site=entry.token, company_name=entry.company_name)
    elif prov == "ashby":
        adapter = AshbyJobSource(board_token=entry.token, company_name=entry.company_name)
    else:
        raise JobSourceConfigError(f"Unsupported ATS provider: {entry.provider}")

    return JobSourceConfig(
        source_id=source_id,
        provider=prov,
        source=adapter,
        company_name=entry.company_name,
        corporate_domain=entry.corporate_domain.strip() if entry.corporate_domain else None,
        enabled=entry.enabled,
    )


def load_job_source_registry(
    raw_config: str | Sequence[dict[str, Any] | HostedAtsSourceEntry] | None,
) -> JobSourceRegistry:
    """Parse, strictly validate, and register hosted ATS source adapters into a registry.

    Fails fast with `JobSourceConfigError` on invalid JSON, unknown provider, missing fields,
    or duplicate source_ids. Zero configured sources returns a valid, empty registry.
    """
    if raw_config is None:
        logger.info("Zero hosted ATS sources configured in settings; registry is empty.")
        return JobSourceRegistry()

    entries_data: Sequence[Any]
    if isinstance(raw_config, str):
        stripped = raw_config.strip()
        if not stripped or stripped == "[]":
            logger.info("Empty hosted ATS sources JSON configured; registry is empty.")
            return JobSourceRegistry()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise JobSourceConfigError(f"Invalid JSON in hosted ATS sources config: {exc}") from exc
        if not isinstance(parsed, list):
            raise JobSourceConfigError("Hosted ATS sources configuration must be a JSON array")
        entries_data = parsed
    else:
        entries_data = raw_config

    if not entries_data:
        logger.info("Zero hosted ATS sources configured; registry is empty.")
        return JobSourceRegistry()

    registry = JobSourceRegistry()
    for idx, item in enumerate(entries_data):
        try:
            if isinstance(item, HostedAtsSourceEntry):
                entry = item
            elif isinstance(item, dict):
                entry = HostedAtsSourceEntry.model_validate(item)
            else:
                raise JobSourceConfigError(
                    f"Configuration entry at index {idx} must be a dict or HostedAtsSourceEntry"
                )
        except Exception as exc:
            if isinstance(exc, JobSourceConfigError):
                raise
            raise JobSourceConfigError(f"Invalid ATS configuration at index {idx}: {exc}") from exc

        cfg = build_job_source_config(entry)
        try:
            registry.register(cfg)
        except ValueError as exc:
            raise JobSourceConfigError(
                f"Failed to register ATS source at index {idx}: {exc}"
            ) from exc

    logger.info(
        "Loaded JobSourceRegistry with %d total sources (%d active).",
        len(registry.list_all()),
        len(registry.list_active()),
    )
    return registry


class JobSourceRegistry:
    """In-memory registry of active JobSource adapters."""

    def __init__(self, configs: Sequence[JobSourceConfig] = ()) -> None:
        self._sources: dict[str, JobSourceConfig] = {}
        for cfg in configs:
            self.register(cfg)

    def register(self, config: JobSourceConfig) -> None:
        """Register a job source config; raises ValueError on duplicate ID."""
        if config.source_id in self._sources:
            raise ValueError(f"Duplicate job source_id registered: '{config.source_id}'")
        self._sources[config.source_id] = config

    def unregister(self, source_id: str) -> None:
        """Remove a job source by ID if present."""
        self._sources.pop(source_id, None)

    def get(self, source_id: str) -> JobSourceConfig | None:
        """Get job source config by ID."""
        return self._sources.get(source_id)

    def list_active(self) -> tuple[JobSourceConfig, ...]:
        """List all currently enabled job source configs."""
        return tuple(cfg for cfg in self._sources.values() if cfg.enabled)

    def list_all(self) -> tuple[JobSourceConfig, ...]:
        """List all registered job source configs regardless of enabled flag."""
        return tuple(self._sources.values())

    def clear(self) -> None:
        """Clear all registered sources (useful for test resets)."""
        self._sources.clear()

    async def check_health(self, source_id: str, ctx: CallContext) -> AdapterHealth:
        """Check operational health of a single source."""
        config = self._sources.get(source_id)
        if config is None:
            return AdapterHealth(
                status=AdapterHealthStatus.UNAVAILABLE,
                checked_at=datetime.now(UTC),
                reason_code="source_not_registered",
            )
        return await config.source.health(ctx)

    async def check_all_health(self, ctx: CallContext) -> dict[str, AdapterHealth]:
        """Check operational health of all enabled sources."""
        results: dict[str, AdapterHealth] = {}
        for cfg in self.list_active():
            try:
                results[cfg.source_id] = await cfg.source.health(ctx)
            except Exception as exc:
                logger.warning(
                    "Unexpected error checking health for source %s: %s",
                    cfg.source_id,
                    exc,
                )
                results[cfg.source_id] = AdapterHealth(
                    status=AdapterHealthStatus.UNAVAILABLE,
                    checked_at=datetime.now(UTC),
                    reason_code="health_check_failed",
                )
        return results


_DEFAULT_REGISTRY = JobSourceRegistry()


def get_default_job_source_registry() -> JobSourceRegistry:
    """Return the global default JobSourceRegistry."""
    return _DEFAULT_REGISTRY
