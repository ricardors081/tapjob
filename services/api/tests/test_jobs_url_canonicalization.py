"""Unit tests for jobs URL canonicalization (WP2.1-A)."""

from tapjob_api.jobs.url_canonicalization import canonicalize_url


def test_canonicalize_url_lowercases_scheme_and_host() -> None:
    assert canonicalize_url("HTTPS://EXAMPLE.COM/jobs/123") == "https://example.com/jobs/123"


def test_canonicalize_url_removes_default_ports() -> None:
    assert canonicalize_url("https://example.com:443/jobs/123") == "https://example.com/jobs/123"
    assert canonicalize_url("http://example.com:80/jobs/123") == "http://example.com/jobs/123"
    assert (
        canonicalize_url("https://example.com:8443/jobs/123") == "https://example.com:8443/jobs/123"
    )


def test_canonicalize_url_removes_fragment() -> None:
    assert (
        canonicalize_url("https://example.com/jobs/123#apply-section")
        == "https://example.com/jobs/123"
    )


def test_canonicalize_url_strips_known_tracking_parameters_only() -> None:
    raw = (
        "https://example.com/jobs/view?"
        "utm_source=linkedin&"
        "gh_jid=456789&"
        "utm_medium=cpc&"
        "fbclid=IwAR123&"
        "ref=job_board&"
        "utm_campaign=hiring2026"
    )
    canonical = canonicalize_url(raw)

    assert "utm_source" not in canonical
    assert "utm_medium" not in canonical
    assert "utm_campaign" not in canonical
    assert "fbclid" not in canonical

    # Provider identity parameters MUST be preserved
    assert "gh_jid=456789" in canonical
    assert "ref=job_board" in canonical


def test_canonicalize_url_normalizes_paths() -> None:
    assert canonicalize_url("https://example.com") == "https://example.com/"
    assert (
        canonicalize_url("https://example.com//careers//openings")
        == "https://example.com/careers/openings"
    )
