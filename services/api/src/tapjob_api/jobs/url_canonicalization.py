"""Conservative URL canonicalization and tracking parameter stripping (WP2.1-A)."""

import urllib.parse

_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_source_platform",
        "fbclid",
        "gclid",
        "msclkid",
        "mc_eid",
        "mc_cid",
        "igshid",
        "yclid",
        "_hsenc",
        "_hsmi",
    }
)


def canonicalize_url(raw_url: str) -> str:
    """Canonicalize a job URL conservatively without breaking provider-specific query identity.

    Rules:
    - Lowercase scheme and host.
    - Omit default ports (80 for http, 443 for https).
    - Strip fragment.
    - Normalize path.
    - Remove only provable tracking query parameters (utm_*, fbclid, etc.).
    - Sort remaining query parameters deterministically.
    """
    parsed = urllib.parse.urlsplit(raw_url.strip())

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Omit default port if explicitly present in netloc
    if ":" in netloc:
        host, _, port_str = netloc.rpartition(":")
        if (scheme == "http" and port_str == "80") or (scheme == "https" and port_str == "443"):
            netloc = host

    path = parsed.path or "/"
    # Clean multiple consecutive slashes in path except root
    while "//" in path:
        path = path.replace("//", "/")

    # Filter query parameters
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered_items = [
        (k, v)
        for k, v in query_items
        if k.lower() not in _TRACKING_PARAMS
        and not any(k.lower().startswith(prefix) for prefix in ("utm_", "mc_"))
    ]
    filtered_items.sort(key=lambda item: (item[0], item[1]))

    canonical_query = urllib.parse.urlencode(filtered_items)

    return urllib.parse.urlunsplit((scheme, netloc, path, canonical_query, ""))
