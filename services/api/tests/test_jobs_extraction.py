"""Unit tests for HTML job extraction and dynamic JS page detection (WP2.1-A)."""

import pytest

from tapjob_api.jobs.errors import JobPageRequiresManualPasteError
from tapjob_api.jobs.extraction import extract_job_from_html


def test_extract_job_from_json_ld_schema() -> None:
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Lead Software Engineer",
            "hiringOrganization": {
                "@type": "Organization",
                "name": "Acme Technologies"
            },
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "addressLocality": "São Paulo",
                    "addressRegion": "SP"
                }
            },
            "description":
                "<p>We are seeking a Lead Software Engineer with strong Python and systems.</p>"
        }
        </script>
    </head>
    <body>
        <div>Fallback text</div>
    </body>
    </html>
    """
    extracted = extract_job_from_html(html)

    assert extracted.title == "Lead Software Engineer"
    assert extracted.company_name == "Acme Technologies"
    assert "We are seeking a Lead Software Engineer" in extracted.description
    assert extracted.location == "São Paulo, SP"
    assert extracted.is_source_declared is True


def test_extract_job_from_opengraph_meta_tags() -> None:
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta property="og:title" content="Staff Data Engineer" />
        <meta property="og:site_name" content="Nubank" />
        <meta property="og:description"
              content="Join our team building event streaming architectures." />
    </head>
    <body>
        <p>Short body content</p>
    </body>
    </html>
    """
    extracted = extract_job_from_html(html)

    assert extracted.title == "Staff Data Engineer"
    assert extracted.company_name == "Nubank"
    assert "Join our team" in extracted.description


def test_extract_job_from_title_heading_fallback() -> None:
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Senior Frontend Developer - Stripe</title>
    </head>
    <body>
        <h1>Senior Frontend Developer</h1>
        <p>Stripe is building infrastructure. We are looking for talented frontend engineers.</p>
    </body>
    </html>
    """
    extracted = extract_job_from_html(html)

    assert "Senior Frontend Developer" in extracted.title
    assert extracted.company_name == "Stripe"
    assert "Stripe is building infrastructure" in extracted.description


def test_extract_job_detects_dynamic_js_page_and_raises_manual_paste_error() -> None:
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Careers Portal</title>
    </head>
    <body>
        <noscript>You need to enable JavaScript to run this app.</noscript>
        <div id="root"></div>
    </body>
    </html>
    """
    with pytest.raises(JobPageRequiresManualPasteError):
        extract_job_from_html(html)
