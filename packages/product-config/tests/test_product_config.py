import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tapjob_product_config import get_product_config, load_product_config

CANONICAL_FILE = Path(__file__).resolve().parents[1] / "product.json"


def _write_config(tmp_path: Path, overrides: dict[str, Any]) -> Path:
    raw = json.loads(CANONICAL_FILE.read_text(encoding="utf-8"))
    raw.update(overrides)
    target = tmp_path / "product.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    return target


def test_canonical_product_config_is_valid() -> None:
    config = load_product_config(CANONICAL_FILE)

    raw = json.loads(CANONICAL_FILE.read_text(encoding="utf-8"))
    assert config.product_name == raw["productName"]
    assert config.default_locale == raw["defaultLocale"]
    assert config.default_currency == raw["defaultCurrency"]


def test_default_loader_resolves_canonical_file() -> None:
    assert get_product_config() == load_product_config(CANONICAL_FILE)


def test_changing_product_name_only_requires_editing_config(tmp_path: Path) -> None:
    config = load_product_config(_write_config(tmp_path, {"productName": "Renamed"}))

    assert config.product_name == "Renamed"


@pytest.mark.parametrize(
    "overrides",
    [
        {"productName": ""},
        {"defaultCurrency": "real"},
        {"defaultLocale": "portuguese"},
        {"deepLinkScheme": "Tap Job"},
        {"unexpectedKey": True},
    ],
)
def test_invalid_product_config_is_rejected(tmp_path: Path, overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        load_product_config(_write_config(tmp_path, overrides))


def test_product_config_is_immutable() -> None:
    config = load_product_config(CANONICAL_FILE)

    with pytest.raises(ValidationError):
        config.product_name = "Mutated"  # type: ignore[misc]
