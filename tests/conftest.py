"""Shared pytest fixtures for the Hikvision Away Arm tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make the custom integration discoverable in every test."""
    yield
