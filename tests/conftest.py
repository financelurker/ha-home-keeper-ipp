"""Test fixtures."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from .helpers import add_printer


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture
async def printer(hass: HomeAssistant):
    """An IPP printer (stub integration) with a black and a cyan ink sensor."""
    return await add_printer(hass)
