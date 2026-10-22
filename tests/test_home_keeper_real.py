"""End-to-end against the real Home Keeper integration (HOME_KEEPER_REF in CI)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.home_keeper_ipp.const import DOMAIN, SOURCE_NS
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .helpers import BLACK, PRINTER_UID, set_level, settle, setup_glue

pytest.importorskip("custom_components.home_keeper", reason="Home Keeper is not installed")


async def _setup_real_hk(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="home_keeper", data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _call(hass: HomeAssistant, service: str, data: dict, *, response: bool = False):
    return await hass.services.async_call(
        "home_keeper", service, data, blocking=True, return_response=response
    )


async def _black(hass: HomeAssistant) -> dict | None:
    resp = await _call(hass, "list_tasks", {}, response=True)
    uid = f"{PRINTER_UID}_marker_0"
    return next(
        (
            t
            for t in resp["tasks"]
            if ((t.get("source") or {}).get(SOURCE_NS) or {}).get("marker_uid") == uid
        ),
        None,
    )


async def _assets(hass: HomeAssistant) -> list[dict]:
    resp = await _call(hass, "list_assets", {}, response=True)
    return [a for a in resp["assets"] if (a.get("source") or {}).get(SOURCE_NS)]


async def test_real_roundtrip(hass: HomeAssistant, printer) -> None:
    await _setup_real_hk(hass)
    await setup_glue(hass)

    task = await _black(hass)
    assert task is not None
    assert task["next_due"]
    assert task["managed_by"]["integration"] == DOMAIN
    assert task["device_id"] == printer[1].id
    assets = await _assets(hass)
    assert len(assets) == 1
    assert task["source"]["part"]["asset_id"] == assets[0]["id"]

    # A manual Done is undone while the printer still reports low.
    await _call(hass, "complete_task", {"task_id": task["id"]})
    await hass.async_block_till_done()
    task = await _black(hass)
    assert task["next_due"]
    assert not task.get("completions")

    # A new cartridge clears it, recording one replacement.
    set_level(hass, BLACK, 100)
    await hass.async_block_till_done()
    task = await _black(hass)
    assert task["next_due"] is None
    assert len(task["completions"]) == 1


async def test_real_disabled_printer_pauses_and_resumes(hass: HomeAssistant, printer) -> None:
    """The paused marker must survive Home Keeper's real ``source`` merge."""
    ipp_entry, _ = printer
    await _setup_real_hk(hass)
    await setup_glue(hass)
    task_id = (await _black(hass))["id"]

    await hass.config_entries.async_set_disabled_by(ipp_entry.entry_id, ConfigEntryDisabler.USER)
    await settle(hass)
    task = await _black(hass)
    assert task["id"] == task_id
    assert task["enabled"] is False
    assert task["source"][SOURCE_NS]["paused"] is True
    assert "part" in task["source"]  # Home Keeper's own namespace is kept

    await hass.config_entries.async_set_disabled_by(ipp_entry.entry_id, None)
    await settle(hass)
    task = await _black(hass)
    assert task["enabled"] is True
    assert "paused" not in task["source"][SOURCE_NS]
    assert task["next_due"]


async def test_real_home_keeper_loaded_after_the_glue(hass: HomeAssistant, printer, caplog) -> None:
    """The boot race: the glue starts first, Home Keeper's services answer later."""
    await setup_glue(hass)
    set_level(hass, BLACK, 5)
    await hass.async_block_till_done()

    await _setup_real_hk(hass)
    await settle(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    task = await _black(hass)
    assert task is not None
    assert task["next_due"]
    assert len(await _assets(hass)) == 1
    assert not [
        r
        for r in caplog.records
        if r.name.startswith("custom_components.home_keeper_ipp") and r.levelno >= 30
    ]


async def test_real_home_keeper_reload_never_duplicates(hass: HomeAssistant, printer) -> None:
    hk_entry = await _setup_real_hk(hass)
    await setup_glue(hass)
    assert await hass.config_entries.async_reload(hk_entry.entry_id)
    await settle(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    resp = await _call(hass, "list_tasks", {}, response=True)
    ours = [t for t in resp["tasks"] if (t.get("source") or {}).get(SOURCE_NS)]
    assert len(ours) == 1
    assert len(await _assets(hass)) == 1
