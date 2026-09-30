"""Typed access to the canonical product identity (``packages/product-config/product.json``).

Runtime code must read product identity (name, locale, currency, deep-link scheme, ...) from here
instead of hardcoding it.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PRODUCT_CONFIG_FILENAME = "product.json"

# Simplified BCP 47 language tag (language + optional region), e.g. "pt-BR".
_LOCALE_PATTERN = r"^[a-z]{2,3}(-[A-Z]{2})?$"
# ISO 4217 alphabetic currency code, e.g. "BRL".
_CURRENCY_PATTERN = r"^[A-Z]{3}$"
# URI scheme syntax from RFC 3986 section 3.1, restricted to lowercase.
_URI_SCHEME_PATTERN = r"^[a-z][a-z0-9+.-]*$"


class _ConfigModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class ProductDomains(_ConfigModel):
    web: str | None
    api: str | None


class ProductConfig(_ConfigModel):
    product_name: str = Field(min_length=1)
    short_name: str = Field(min_length=1)
    legal_name: str | None
    default_locale: str = Field(pattern=_LOCALE_PATTERN)
    default_currency: str = Field(pattern=_CURRENCY_PATTERN)
    deep_link_scheme: str = Field(pattern=_URI_SCHEME_PATTERN)
    support_email: str | None
    domains: ProductDomains


def _default_config_path() -> Path:
    package_dir = Path(__file__).resolve().parent
    bundled = package_dir / PRODUCT_CONFIG_FILENAME
    if bundled.is_file():
        # Installed wheel: product.json is force-included next to this module.
        return bundled
    # Source checkout / editable install: the canonical file sits at the package root, where the
    # TypeScript consumers read it too.
    return package_dir.parents[1] / PRODUCT_CONFIG_FILENAME


def load_product_config(path: Path | None = None) -> ProductConfig:
    """Load and validate product configuration. Raises ``pydantic.ValidationError`` if invalid."""
    config_path = path or _default_config_path()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return ProductConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_product_config() -> ProductConfig:
    """Process-wide cached product configuration."""
    return load_product_config()


__all__ = [
    "PRODUCT_CONFIG_FILENAME",
    "ProductConfig",
    "ProductDomains",
    "get_product_config",
    "load_product_config",
]
