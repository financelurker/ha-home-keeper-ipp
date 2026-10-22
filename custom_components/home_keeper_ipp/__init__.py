"""Home Keeper ↔ IPP glue integration.

Watches the ink and toner level sensors of Home Assistant's IPP integration and keeps
one Home Keeper ``triggered`` task per cartridge: armed when the level drops to the
low limit, cleared when the printer reports a new cartridge. Optionally keeps a
managed appliance per printer whose parts count the spare cartridges.

Stateless: nothing is stored here. Tasks are found again through our ``source``
namespace (keyed on the marker sensor's registry unique id) on every start.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import logic
from .const import (
    HK_DOMAIN,
    HK_SERVICE_DELETE_ASSET,
    HK_SERVICE_DELETE_TASK,
    HK_SERVICE_LIST_ASSETS,
    HK_SERVICE_LIST_TASKS,
    HK_SERVICE_UPDATE_ASSET,
)
from .wiring import IppGlue

_LOGGER = logging.getLogger(__name__)

type IppGlueConfigEntry = ConfigEntry[IppGlue]


async def async_setup_entry(hass: HomeAssistant, entry: IppGlueConfigEntry) -> bool:
    """Set up the glue from its config entry."""
    glue = IppGlue(hass, entry)
    entry.runtime_data = glue
    await glue.async_setup()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: IppGlueConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: IppGlueConfigEntry) -> bool:
    """Unload the entry; listeners go through ``entry.async_on_unload``.

    The tasks stay: an unload is often a reload (an options change), and the
    replacement history must survive it. ``async_remove_entry`` cleans up for good.
    """
    return True


async def async_remove_entry(hass: HomeAssistant, entry: IppGlueConfigEntry) -> None:
    """On removal, delete our tasks and hand the spare-cartridge appliances back."""
    services = hass.services
    if services.has_service(HK_DOMAIN, HK_SERVICE_LIST_TASKS):
        try:
            resp = await services.async_call(
                HK_DOMAIN, HK_SERVICE_LIST_TASKS, {}, blocking=True, return_response=True
            )
            for task in logic.our_tasks(list((resp or {}).get("tasks", []))):
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_DELETE_TASK,
                    {"task_id": task["id"], "force": True},
                    blocking=True,
                )
        except Exception:
            _LOGGER.debug("Could not remove the cartridge tasks", exc_info=True)

    if not services.has_service(HK_DOMAIN, HK_SERVICE_LIST_ASSETS):
        return
    try:
        resp = await services.async_call(
            HK_DOMAIN, HK_SERVICE_LIST_ASSETS, {}, blocking=True, return_response=True
        )
        for asset in logic.our_assets(list((resp or {}).get("assets", []))):
            counted = any(p.get("stock") is not None for p in asset.get("parts") or [])
            if counted:
                # The spares are still in the drawer: keep the appliance and its
                # counts, drop only our ownership (INTEGRATING.md §8, "Uninstall").
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_UPDATE_ASSET,
                    {"asset_id": asset["id"], "managed_by": None},
                    blocking=True,
                )
            elif services.has_service(HK_DOMAIN, HK_SERVICE_DELETE_ASSET):
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_DELETE_ASSET,
                    {"asset_id": asset["id"], "force": True},
                    blocking=True,
                )
    except Exception:
        _LOGGER.debug("Could not release the spare-cartridge appliances", exc_info=True)
