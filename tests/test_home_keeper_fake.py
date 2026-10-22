"""End-to-end against Home Keeper's test fake: IPP marker sensors → glue → tasks."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.home_keeper_ipp.const import OPT_EXCLUDED_ENTITIES, SOURCE_NS
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .helpers import (
    BLACK,
    CYAN,
    PRINTER_UID,
    set_level,
    settle as _settle,
    setup_glue as _setup_glue,
)

models = pytest.importorskip(
    "custom_components.home_keeper.models", reason="Home Keeper is not installed"
)
FakeHomeKeeper = pytest.importorskip(
    "custom_components.home_keeper.testing", reason="Home Keeper is not installed"
).FakeHomeKeeper


class _Fake(FakeHomeKeeper):
    """Home Keeper's fake, merging ``source`` on ``update_task`` like the real store.

    The real ``store.update_task`` merges ``source`` per namespace with
    ``models.merge_source``; the fake passes the update to ``models.merge_update``,
    which does not read ``source``, so an integration's own namespace never changes
    there. This applies the same merge, so the tests see production behaviour.
    """

    async def _update_task(self, call) -> None:
        source_update = call.data.get("source")
        task_id = call.data["task_id"]
        before = self.tasks[task_id].get("source")
        await super()._update_task(call)
        if source_update:
            self.tasks[task_id]["source"] = models.merge_source(
                before, source_update, reserved=("part", "buy", "declarative_companion")
            )


async def async_setup_fake_home_keeper(
    hass: HomeAssistant, *, state: ConfigEntryState = ConfigEntryState.LOADED
) -> _Fake:
    """The fake's services, plus a Home Keeper entry in *state*.

    The glue only talks to Home Keeper while an entry of it is loaded, like Home
    Keeper's own services, which answer ``integration_not_loaded`` otherwise.
    """
    fake = _Fake(hass)
    fake.register()
    fake.entry = MockConfigEntry(domain="home_keeper", state=state)
    fake.entry.add_to_hass(hass)
    return fake


def _task(hk, index: int):
    return hk.get_task_by_source(SOURCE_NS, marker_uid=f"{PRINTER_UID}_marker_{index}")


def _part(hk, name: str):
    asset = next(iter(hk.assets.values()))
    return hk.part_named(asset["id"], name)


async def test_low_cartridge_task_with_stock_and_refill(hass, printer):
    _, device = printer
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)

    black = _task(hk, 0)
    assert black is not None
    assert black["next_due"]  # already low at start → armed
    assert black["device_id"] == device.id
    assert black["name"] == "Replace Black ink (HP OfficeJet)"
    assert _task(hk, 1) is None  # 60 % is fine

    # One spare-cartridge appliance for the printer, the task drawing from its part.
    assert len(hk.assets) == 1
    part = _part(hk, "Black ink")
    assert black["source"]["part"]["part_id"] == part["id"]
    part["stock"] = 2  # the user counts two spares

    # Wobbling near the line does nothing; a new cartridge clears and draws a spare.
    set_level(hass, BLACK, 20)
    await hass.async_block_till_done()
    assert _task(hk, 0)["next_due"]
    set_level(hass, BLACK, 100)
    await hass.async_block_till_done()
    black = _task(hk, 0)
    assert black["next_due"] is None
    assert len(black["completions"]) == 1
    assert _part(hk, "Black ink")["stock"] == 1

    # Running low again re-arms the same task (history kept).
    set_level(hass, BLACK, 12)
    await hass.async_block_till_done()
    black = _task(hk, 0)
    assert black["next_due"]
    assert len(black["completions"]) == 1


async def test_manual_completion_is_undone_and_stock_returned(hass, printer):
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    _part(hk, "Black ink")["stock"] = 3

    hk.fire_user_completion(_task(hk, 0)["id"])
    await hass.async_block_till_done()

    black = _task(hk, 0)
    assert black["next_due"]  # armed again
    assert not black.get("completions")
    assert _part(hk, "Black ink")["stock"] == 3


async def test_disabled_printer_pauses_and_resumes(hass, printer):
    ipp_entry, _ = printer
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    task_id = _task(hk, 0)["id"]

    await hass.config_entries.async_set_disabled_by(ipp_entry.entry_id, ConfigEntryDisabler.USER)
    await _settle(hass)
    black = _task(hk, 0)
    assert black["id"] == task_id  # kept, not deleted
    assert black["enabled"] is False
    assert black["source"][SOURCE_NS]["paused"] is True

    # While disabled, nothing reacts (the sensor would be gone anyway).
    set_level(hass, BLACK, 100)
    await hass.async_block_till_done()
    assert _task(hk, 0)["next_due"]

    # A new cartridge went in meanwhile: re-enabling resumes the task and clears it.
    await hass.config_entries.async_set_disabled_by(ipp_entry.entry_id, None)
    await _settle(hass)
    black = _task(hk, 0)
    assert black["id"] == task_id
    assert black["enabled"] is True
    assert "paused" not in black["source"][SOURCE_NS]
    assert black["next_due"] is None
    assert len(black["completions"]) == 1


async def test_single_disabled_sensor_pauses_only_its_task(hass, printer):
    hk = await async_setup_fake_home_keeper(hass)
    set_level(hass, CYAN, 5)
    await _setup_glue(hass)

    er.async_get(hass).async_update_entity(BLACK, disabled_by=er.RegistryEntryDisabler.USER)
    await _settle(hass)
    assert _task(hk, 0)["enabled"] is False
    assert _task(hk, 1)["enabled"] is True

    er.async_get(hass).async_update_entity(BLACK, disabled_by=None)
    await _settle(hass)
    assert _task(hk, 0)["enabled"] is True


async def test_task_switched_off_by_user_stays_off(hass, printer):
    ipp_entry, _ = printer
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    _task(hk, 0)["enabled"] = False  # the user switches it off in Home Keeper

    await hass.config_entries.async_set_disabled_by(ipp_entry.entry_id, ConfigEntryDisabler.USER)
    await _settle(hass)
    await hass.config_entries.async_set_disabled_by(ipp_entry.entry_id, None)
    await _settle(hass)
    assert _task(hk, 0)["enabled"] is False


async def test_deleted_printer_entry_deletes_tasks_keeps_counted_stock(hass, printer):
    ipp_entry, _ = printer
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    assert _task(hk, 0) is not None
    _part(hk, "Black ink")["stock"] = 1

    await hass.config_entries.async_remove(ipp_entry.entry_id)
    await _settle(hass)
    assert _task(hk, 0) is None
    assert _part(hk, "Black ink")["stock"] == 1  # the spare is still in the drawer


async def test_excluded_sensor_loses_its_task(hass, printer):
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)
    assert _task(hk, 0) is not None

    hass.config_entries.async_update_entry(entry, options={OPT_EXCLUDED_ENTITIES: [BLACK]})
    await _settle(hass)
    assert _task(hk, 0) is None


async def test_german_names_and_glue_removal(hass, printer):
    hass.config.language = "de"
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)
    assert _task(hk, 0)["name"] == "Black ink tauschen (HP OfficeJet)"
    _part(hk, "Black ink")["stock"] = 2

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _task(hk, 0) is None
    asset = next(iter(hk.assets.values()))
    assert asset.get("managed_by") is None  # handed back, counts kept


async def test_without_home_keeper_nothing_breaks(hass, printer):
    entry = await _setup_glue(hass)
    set_level(hass, BLACK, 3)
    await hass.async_block_till_done()
    assert entry.state.name == "LOADED"


async def test_printer_low_level_decides_when_reported(hass, printer):
    hk = await async_setup_fake_home_keeper(hass)
    set_level(hass, BLACK, 60)
    set_level(hass, CYAN, 18, low_level=20)  # printer: low below 20 %
    await _setup_glue(hass)
    assert _task(hk, 1)["next_due"]  # 18 ≤ 20, though above the fixed 15 %
    assert "vom Drucker" not in _task(hk, 1)["notes"]  # English instance
    assert "set by the printer" in _task(hk, 1)["notes"]

    set_level(hass, CYAN, 35, low_level=20)  # 35 < 20 + 20: not yet a new cartridge
    await hass.async_block_till_done()
    assert _task(hk, 1)["next_due"]
    set_level(hass, CYAN, 40, low_level=20)
    await hass.async_block_till_done()
    assert _task(hk, 1)["next_due"] is None


async def test_boot_race_waits_for_home_keeper_without_warnings(hass, printer, caplog):
    """Home Keeper's services exist before its entry is loaded (boot, reload)."""
    hk = await async_setup_fake_home_keeper(hass, state=ConfigEntryState.SETUP_IN_PROGRESS)
    calls: list[str] = []
    hass.bus.async_listen("call_service", lambda e: calls.append(e.data["service"]))
    await _setup_glue(hass)
    set_level(hass, BLACK, 5)
    set_level(hass, BLACK, 4)
    await hass.async_block_till_done()
    assert calls == []
    assert hk.tasks == {}  # nothing called while not loaded

    # Home Keeper pings from inside its setup — still not loaded at that moment.
    hass.bus.async_fire("home_keeper_register_companions")
    await hass.async_block_till_done()
    assert hk.tasks == {}

    # Its setup finishes; the pending catch-up does the work, exactly once.
    hk.entry.mock_state(hass, ConfigEntryState.LOADED)
    await _settle(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert len([t for t in hk.tasks.values() if t["source"].get(SOURCE_NS)]) == 1
    assert _task(hk, 0)["next_due"]
    assert len(hk.assets) == 1
    assert not [
        r
        for r in caplog.records
        if r.name.startswith("custom_components.home_keeper_ipp") and r.levelno >= 30
    ]


async def test_reload_of_home_keeper_never_duplicates(hass, printer):
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    assert len(hk.tasks) == 1

    hk.entry.mock_state(hass, ConfigEntryState.NOT_LOADED)  # mid-reload
    set_level(hass, BLACK, 3)
    await hass.async_block_till_done()
    hk.entry.mock_state(hass, ConfigEntryState.LOADED)
    hass.bus.async_fire("home_keeper_register_companions")
    await _settle(hass)
    assert len(hk.tasks) == 1
    assert len(hk.assets) == 1
