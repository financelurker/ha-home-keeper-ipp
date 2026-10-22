"""Shared helpers: a stub IPP printer, its marker sensors, and the glue."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    async_fire_time_changed,
    mock_integration,
)

from custom_components.home_keeper_ipp.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

PRINTER_UID = "cfe92100-67c4-11d4-a45f-f8d027761251"
BLACK = "sensor.hp_officejet_black_ink"
CYAN = "sensor.hp_officejet_cyan_ink"


async def settle(hass: HomeAssistant) -> None:
    """Let the registry debounce fire and every job finish."""
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()


def set_level(hass: HomeAssistant, entity_id: str, level, low_level=0) -> None:
    hass.states.async_set(
        entity_id,
        str(level),
        {
            "marker_type": "ink-cartridge",
            "marker_low_level": low_level,
            "marker_high_level": 100,
            "unit_of_measurement": "%",
        },
    )


async def setup_glue(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def add_printer(hass: HomeAssistant):
    """An IPP printer entry with a device and two marker sensors in the registry.

    The IPP integration itself is a stub: the glue only reads the registry and the
    states, and re-enabling the entry must not try to reach a real printer.
    """

    async def _setup_entry(hass, entry):
        return True

    mock_integration(
        hass, MockModule("ipp", async_setup_entry=_setup_entry, async_unload_entry=_setup_entry)
    )
    ipp_entry = MockConfigEntry(domain="ipp", title="HP OfficeJet")
    ipp_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=ipp_entry.entry_id,
        identifiers={("ipp", PRINTER_UID)},
        name="HP OfficeJet",
    )
    ent_reg = er.async_get(hass)
    for index, (object_id, name) in enumerate(
        [("hp_officejet_black_ink", "Black ink"), ("hp_officejet_cyan_ink", "Cyan ink")]
    ):
        ent_reg.async_get_or_create(
            "sensor",
            "ipp",
            f"{PRINTER_UID}_marker_{index}",
            suggested_object_id=object_id,
            config_entry=ipp_entry,
            device_id=device.id,
            original_name=name,
            translation_key="marker",
        )
    set_level(hass, BLACK, 8)
    set_level(hass, CYAN, 60)
    return ipp_entry, device
