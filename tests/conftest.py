"""Shared fixtures."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """--live runs the tests against the real fairgrow.eu."""
    parser.addoption("--live", action="store_true", default=False)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live tests unless --live is given."""
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (talks to the real fairgrow.eu)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/fairgrow in every test."""
    return
