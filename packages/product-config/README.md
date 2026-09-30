# @tapjob/product-config

Single source of truth for product identity (D-022, `docs/DESIGN_SYSTEM.md` §6).

- `product.json` — canonical, versioned values: product name, default locale/currency, deep-link scheme, public domains.
- `src/index.ts` — typed read-only access for TypeScript consumers (mobile shell arrives in WP0.1-B).
- `src/tapjob_product_config/` — validated Python loader used by the backend. Its Pydantic model is the schema; `pytest` validates the canonical file in CI.

Rules:

- Runtime code never hardcodes the product name or these defaults; it reads this package.
- Only public, non-secret values belong here. Deployment-specific values are environment config; secrets stay server-side.
