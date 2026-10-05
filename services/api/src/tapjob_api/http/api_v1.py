"""Versioned business API root (docs/adr/0003-api-contract-client-generation.md).

`/health` and `/ready` stay outside versioning -- they are operational endpoints, not product
contract (docs/ENGINEERING_STANDARDS.md §7).

This router exists so `main.py` has one stable mount point and so every future business route
inherits API_V1_PREFIX automatically instead of each PR choosing its own path (enforced by
tapjob_api.http.contract_rules.validate_api_v1_scoping). WP1.1 (docs/adr/0009-identity-auth-user-
account.md) is the first business feature mounted here.
"""

from fastapi import APIRouter

from tapjob_api.identity.router import router as identity_router

API_V1_PREFIX = "/api/v1"

router = APIRouter(prefix=API_V1_PREFIX)
router.include_router(identity_router)
