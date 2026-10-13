"""Versioned business API root (docs/adr/0003-api-contract-client-generation.md).

`/health` and `/ready` stay outside versioning -- they are operational endpoints, not product
contract (docs/ENGINEERING_STANDARDS.md §7).

This router exists so `main.py` has one stable mount point and so every future business route
inherits API_V1_PREFIX automatically instead of each PR choosing its own path (enforced by
tapjob_api.http.contract_rules.validate_api_v1_scoping). WP1.1 (docs/adr/0009-identity-auth-user-
account.md) was the first business feature mounted here; WP1.2 (docs/adr/0010-career-profile-
truth-store.md) is the second; WP1.3 (docs/adr/0011-resume-import.md) is the third.
"""

from fastapi import APIRouter

from tapjob_api.career_profile.router import router as career_profile_router
from tapjob_api.identity.router import router as identity_router
from tapjob_api.jobs.companies_router import router as companies_router
from tapjob_api.jobs.router import router as jobs_router
from tapjob_api.matching.router import router as matching_router
from tapjob_api.resume_import.router import router as resume_import_router

API_V1_PREFIX = "/api/v1"

router = APIRouter(prefix=API_V1_PREFIX)
router.include_router(identity_router)
router.include_router(career_profile_router)
router.include_router(resume_import_router)
router.include_router(jobs_router)
router.include_router(companies_router)
router.include_router(matching_router)
