"""Pytest fixtures for the Eliis integration tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from .fakes import FakeEliisClient


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in the test Home Assistant instance."""
    yield


@pytest.fixture(autouse=True)
def patch_runtime_client():
    """Keep entry setup off the network and reset fake state between tests."""
    FakeEliisClient.reset()
    with patch("custom_components.eliis.runtime.EliisClient", FakeEliisClient):
        yield
