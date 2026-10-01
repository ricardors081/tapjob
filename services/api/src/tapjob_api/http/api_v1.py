"""Versioned business API root (docs/adr/0003-api-contract-client-generation.md).

`/health` and `/ready` stay outside versioning -- they are operational endpoints, not product
contract (docs/ENGINEERING_STANDARDS.md §7).

No product route exists yet: WP0.2 ships contracts, not features (docs/IMPLEMENTATION_PLAN.md
WP0.2). This router exists now so `main.py` has one stable mount point and so every future
business route inherits API_V1_PREFIX automatically instead of each PR choosing its own path
(enforced by tapjob_api.http.contract_rules.validate_api_v1_scoping).
"""

from fastapi import APIRouter

API_V1_PREFIX = "/api/v1"

router = APIRouter(prefix=API_V1_PREFIX)
