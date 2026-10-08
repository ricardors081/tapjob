"""Unit tests for JobSourceRegistry and JobSourceConfig (WP2.1-B)."""

import asyncio
from datetime import UTC, datetime

import pytest

from tapjob_api.jobs.sources.registry import (
    JobSourceConfig,
    JobSourceRegistry,
    get_default_job_source_registry,
    load_job_source_registry,
)
from tapjob_api.ports import (
    AdapterHealth,
    AdapterHealthStatus,
    CallContext,
    CapabilityManifest,
    CapabilityReport,
    CapabilityStatus,
    DiscoveryPage,
    ExternalJobRef,
    JobDetails,
    JobSourceCapability,
)


class DummyJobSource:
    provider: str

    def __init__(
        self, provider: str, status: AdapterHealthStatus = AdapterHealthStatus.HEALTHY
    ) -> None:
        self.provider = provider
        self._status = status

    async def capabilities(self, ctx: CallContext) -> CapabilityManifest[JobSourceCapability]:
        return CapabilityManifest(
            [CapabilityReport(JobSourceCapability.DISCOVERY, CapabilityStatus.SUPPORTED)]
        )

    async def discover(self, cursor: str | None, ctx: CallContext) -> DiscoveryPage:
        return DiscoveryPage(items=(), next_cursor=None)

    async def fetch_details(self, ref: ExternalJobRef, ctx: CallContext) -> JobDetails:
        return JobDetails(ref=ref, description_raw="Dummy", fetched_at=datetime.now(UTC))

    async def health(self, ctx: CallContext) -> AdapterHealth:
        return AdapterHealth(status=self._status, checked_at=datetime.now(UTC))


def _ctx() -> CallContext:
    return CallContext.with_timeout("test-corr", 10.0)


def test_registry_register_and_lookup() -> None:
    registry = JobSourceRegistry()
    cfg = JobSourceConfig(
        source_id="src-1",
        provider="greenhouse",
        source=DummyJobSource("greenhouse"),
        company_name="Acme",
    )
    registry.register(cfg)

    assert registry.get("src-1") == cfg
    assert len(registry.list_active()) == 1
    assert len(registry.list_all()) == 1


def test_registry_duplicate_registration_rejected() -> None:
    registry = JobSourceRegistry()
    cfg1 = JobSourceConfig(
        source_id="src-1",
        provider="greenhouse",
        source=DummyJobSource("greenhouse"),
        company_name="Acme",
    )
    cfg2 = JobSourceConfig(
        source_id="src-1",
        provider="lever",
        source=DummyJobSource("lever"),
        company_name="Acme 2",
    )
    registry.register(cfg1)
    with pytest.raises(ValueError, match="Duplicate job source_id"):
        registry.register(cfg2)


def test_registry_unregister_and_clear() -> None:
    registry = JobSourceRegistry()
    cfg1 = JobSourceConfig(
        source_id="src-1",
        provider="greenhouse",
        source=DummyJobSource("greenhouse"),
        company_name="Acme",
    )
    registry.register(cfg1)
    assert registry.get("src-1") is not None

    registry.unregister("src-1")
    assert registry.get("src-1") is None

    registry.register(cfg1)
    registry.clear()
    assert len(registry.list_all()) == 0


def test_registry_list_active_filters_disabled() -> None:
    registry = JobSourceRegistry()
    cfg_active = JobSourceConfig(
        source_id="src-active",
        provider="greenhouse",
        source=DummyJobSource("greenhouse"),
        company_name="Active Corp",
        enabled=True,
    )
    cfg_disabled = JobSourceConfig(
        source_id="src-disabled",
        provider="lever",
        source=DummyJobSource("lever"),
        company_name="Disabled Corp",
        enabled=False,
    )
    registry.register(cfg_active)
    registry.register(cfg_disabled)

    active = registry.list_active()
    assert len(active) == 1
    assert active[0].source_id == "src-active"

    all_sources = registry.list_all()
    assert len(all_sources) == 2


def test_registry_check_health() -> None:
    async def scenario() -> None:
        registry = JobSourceRegistry()
        cfg1 = JobSourceConfig(
            source_id="gh-1",
            provider="greenhouse",
            source=DummyJobSource("greenhouse", AdapterHealthStatus.HEALTHY),
            company_name="Acme",
        )
        cfg2 = JobSourceConfig(
            source_id="lv-1",
            provider="lever",
            source=DummyJobSource("lever", AdapterHealthStatus.DEGRADED),
            company_name="Beta",
        )
        registry.register(cfg1)
        registry.register(cfg2)

        health1 = await registry.check_health("gh-1", _ctx())
        assert health1.status == AdapterHealthStatus.HEALTHY

        all_health = await registry.check_all_health(_ctx())
        assert all_health["gh-1"].status == AdapterHealthStatus.HEALTHY
        assert all_health["lv-1"].status == AdapterHealthStatus.DEGRADED

        unregistered = await registry.check_health("missing", _ctx())
        assert unregistered.status == AdapterHealthStatus.UNAVAILABLE
        assert unregistered.reason_code == "source_not_registered"

    asyncio.run(scenario())


def test_default_registry_singleton() -> None:
    reg = get_default_job_source_registry()
    assert isinstance(reg, JobSourceRegistry)


# ==============================================================================
# TYPED CONFIGURATION & REGISTRY LOADER TESTS (Finding 1)
# ==============================================================================


def test_load_registry_empty_or_none() -> None:
    reg1 = load_job_source_registry(None)
    assert len(reg1.list_all()) == 0

    reg2 = load_job_source_registry("")
    assert len(reg2.list_all()) == 0

    reg3 = load_job_source_registry("[]")
    assert len(reg3.list_all()) == 0

    reg4 = load_job_source_registry([])
    assert len(reg4.list_all()) == 0


def test_load_registry_valid_json_configures_real_adapters() -> None:
    from tapjob_api.jobs.sources.ashby import AshbyJobSource
    from tapjob_api.jobs.sources.greenhouse import GreenhouseJobSource
    from tapjob_api.jobs.sources.lever import LeverJobSource

    raw_json = """
    [
        {
            "provider": "greenhouse",
            "token": "canonical",
            "company_name": "Canonical Ltd",
            "corporate_domain": "canonical.com",
            "enabled": true
        },
        {
            "provider": "lever",
            "token": "netflix",
            "company_name": "Netflix Inc",
            "enabled": false,
            "source_id": "custom-netflix"
        },
        {
            "provider": "ashby",
            "token": "monzo",
            "company_name": "Monzo Bank",
            "enabled": true
        }
    ]
    """
    registry = load_job_source_registry(raw_json)
    assert len(registry.list_all()) == 3
    assert len(registry.list_active()) == 2

    # Deterministic source_id: provider:token
    gh_cfg = registry.get("greenhouse:canonical")
    assert gh_cfg is not None
    assert gh_cfg.company_name == "Canonical Ltd"
    assert gh_cfg.corporate_domain == "canonical.com"
    assert gh_cfg.enabled is True
    assert isinstance(gh_cfg.source, GreenhouseJobSource)
    assert gh_cfg.source.board_token == "canonical"  # noqa: S105

    # Custom source_id respected
    lv_cfg = registry.get("custom-netflix")
    assert lv_cfg is not None
    assert lv_cfg.enabled is False
    assert isinstance(lv_cfg.source, LeverJobSource)
    assert lv_cfg.source.site == "netflix"

    # Ashby source
    ashby_cfg = registry.get("ashby:monzo")
    assert ashby_cfg is not None
    assert ashby_cfg.enabled is True
    assert isinstance(ashby_cfg.source, AshbyJobSource)
    assert ashby_cfg.source.board_token == "monzo"  # noqa: S105


def test_load_registry_unknown_provider_fails_closed() -> None:
    from tapjob_api.jobs.sources.registry import JobSourceConfigError

    bad_json = '[{"provider": "workday", "token": "foo", "company_name": "Acme"}]'
    with pytest.raises(JobSourceConfigError) as exc_info:
        load_job_source_registry(bad_json)
    assert "Unknown ATS provider 'workday'" in str(exc_info.value)


def test_load_registry_missing_required_fields_fails_closed() -> None:
    from tapjob_api.jobs.sources.registry import JobSourceConfigError

    # Missing token
    with pytest.raises(JobSourceConfigError):
        load_job_source_registry(
            '[{"provider": "greenhouse", "token": "", "company_name": "Acme"}]'
        )

    # Missing company_name
    with pytest.raises(JobSourceConfigError):
        load_job_source_registry(
            '[{"provider": "greenhouse", "token": "acme", "company_name": ""}]'
        )


def test_load_registry_malformed_json_fails_closed() -> None:
    from tapjob_api.jobs.sources.registry import JobSourceConfigError

    with pytest.raises(JobSourceConfigError):
        load_job_source_registry("{invalid json")

    with pytest.raises(JobSourceConfigError):
        load_job_source_registry('{"not": "a list"}')


def test_load_registry_duplicate_source_id_fails_closed() -> None:
    from tapjob_api.jobs.sources.registry import JobSourceConfigError

    dup_json = """
    [
        {"provider": "greenhouse", "token": "acme", "company_name": "Acme 1"},
        {"provider": "greenhouse", "token": "acme", "company_name": "Acme 2"}
    ]
    """
    with pytest.raises(JobSourceConfigError):
        load_job_source_registry(dup_json)


def test_load_registry_typo_in_enabled_fails_closed() -> None:
    from tapjob_api.jobs.sources.registry import JobSourceConfigError

    # Typo in field name like "enabledd" must fail closed and never be silently ignored
    bad_json = """
    [
        {
            "provider": "greenhouse",
            "token": "acme",
            "company_name": "Acme Corp",
            "enabledd": false
        }
    ]
    """
    with pytest.raises(JobSourceConfigError) as exc_info:
        load_job_source_registry(bad_json)
    assert "extra_forbidden" in str(exc_info.value) or "enabledd" in str(exc_info.value)


def test_load_registry_unknown_extra_field_fails_closed() -> None:
    from tapjob_api.jobs.sources.registry import JobSourceConfigError

    bad_json = """
    [
        {
            "provider": "lever",
            "token": "netflix",
            "company_name": "Netflix",
            "unknown_bogus_field": 123
        }
    ]
    """
    with pytest.raises(JobSourceConfigError) as exc_info:
        load_job_source_registry(bad_json)
    assert "extra_forbidden" in str(exc_info.value) or "unknown_bogus_field" in str(exc_info.value)
