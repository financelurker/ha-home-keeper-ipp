"""Home Assistant wiring for the IPP glue.

Reads the IPP marker sensors out of the entity registry, follows their states, and
turns the pure decisions in :mod:`logic` into Home Keeper service calls. Every call
that crosses into Home Keeper is guarded with ``has_service``, so the glue does
nothing (and breaks nothing) while Home Keeper is absent or reloading.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from . import logic
from .const import (
    COMPANION_ICON,
    DEFAULT_CLEAR_ON_REFILL,
    DEFAULT_NAME_TEMPLATE,
    DEFAULT_RECOVERY_MARGIN,
    DEFAULT_STOCK_ENABLED,
    DEFAULT_THRESHOLD,
    DOCS_URL,
    DOMAIN,
    HK_DOMAIN,
    HK_EVENT_REGISTER_COMPANIONS,
    HK_EVENT_TASK_COMPLETED,
    HK_SERVICE_ADD_ASSET,
    HK_SERVICE_ADD_TASK,
    HK_SERVICE_COMPLETE_TASK,
    HK_SERVICE_DELETE_TASK,
    HK_SERVICE_LIST_ASSETS,
    HK_SERVICE_LIST_TASKS,
    HK_SERVICE_REGISTER_COMPANION,
    HK_SERVICE_SET_TASK_CONSUMABLE,
    HK_SERVICE_TRIGGER_TASK,
    HK_SERVICE_UPDATE_MANAGED_ASSET,
    HK_SERVICE_UPDATE_TASK,
    IPP_ATTR_MARKER_LOW_LEVEL,
    IPP_ATTR_MARKER_TYPE,
    IPP_DOMAIN,
    IPP_MARKER_TRANSLATION_KEY,
    IPP_MARKER_UNIQUE_ID_RE,
    OPT_CLEAR_ON_REFILL,
    OPT_EXCLUDED_ENTITIES,
    OPT_NAME_TEMPLATE,
    OPT_RECOVERY_MARGIN,
    OPT_STOCK_ENABLED,
    OPT_THRESHOLD,
    ORIGIN,
    SOURCE_NS,
    TEXT,
)

_LOGGER = logging.getLogger(__name__)

HK_SERVICE_DELETE_COMPLETION = "delete_completion"


class HomeKeeperUnavailable(Exception):
    """Home Keeper's services exist but its entry is not loaded (boot, reload, off).

    Home Keeper registers its services once per run and keeps them while its entry
    loads, reloads, is disabled or failed; a call then raises
    ``integration_not_loaded``. A pass that meets this stops: acting on a task list
    it could not read would create duplicates. Home Keeper fires
    ``home_keeper_register_companions`` once it is loaded again, and the glue
    reconciles then.
    """


# Registry changes arrive in bursts (a printer brings all its sensors at once).
_REGISTRY_DEBOUNCE_SECONDS = 2.0
# While Home Keeper is not loaded, check again this often, this many times (5 min).
# Home Keeper asks companions to register from inside its setup, before its entry
# counts as loaded and before its services answer, so that ping alone can't be the
# moment to catch up.
_CATCH_UP_SECONDS = 3.0
_CATCH_UP_ATTEMPTS = 100


def is_marker_entry(entry: er.RegistryEntry) -> bool:
    """Whether *entry* is an IPP marker (ink / toner level) sensor."""
    if entry.platform != IPP_DOMAIN or entry.domain != "sensor":
        return False
    if entry.translation_key == IPP_MARKER_TRANSLATION_KEY:
        return True
    return bool(re.search(IPP_MARKER_UNIQUE_ID_RE, entry.unique_id or ""))


class IppGlue:
    """Follows the IPP marker sensors and drives Home Keeper cartridge tasks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Keep the entry; nothing is read or called until :meth:`async_setup`."""
        self.hass = hass
        self.entry = entry
        # Serialises read-tasks → decide → call, so two quick state changes can't
        # both see "no task" and create two.
        self._lock = asyncio.Lock()
        self._markers: dict[str, logic.Marker] = {}  # entity_id → marker
        self._unsub_state: CALLBACK_TYPE | None = None
        self._cancel_started: CALLBACK_TYPE | None = None
        self._cancel_debounce: CALLBACK_TYPE | None = None
        self._hk_down = False  # logged once per outage, not once per call
        self._cancel_catch_up: CALLBACK_TYPE | None = None
        self._catch_up_attempts = 0

    # ── options ──────────────────────────────────────────────────────────────
    @property
    def settings(self) -> logic.Settings:
        """Return the options, resolved."""
        opts = self.entry.options
        return logic.Settings(
            config_entry_id=self.entry.entry_id,
            threshold=float(opts.get(OPT_THRESHOLD, DEFAULT_THRESHOLD)),
            recovery_margin=float(opts.get(OPT_RECOVERY_MARGIN, DEFAULT_RECOVERY_MARGIN)),
            clear_on_refill=bool(opts.get(OPT_CLEAR_ON_REFILL, DEFAULT_CLEAR_ON_REFILL)),
            name_template=str(opts.get(OPT_NAME_TEMPLATE, DEFAULT_NAME_TEMPLATE) or ""),
            lang=logic.lang_of(self.hass.config.language),
        )

    @property
    def _stock_enabled(self) -> bool:
        return bool(self.entry.options.get(OPT_STOCK_ENABLED, DEFAULT_STOCK_ENABLED))

    @property
    def _excluded(self) -> set[str]:
        return set(self.entry.options.get(OPT_EXCLUDED_ENTITIES) or [])

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def async_setup(self) -> None:
        """Subscribe to events and reconcile once Home Assistant runs."""
        bus = self.hass.bus
        on_unload = self.entry.async_on_unload
        on_unload(bus.async_listen(HK_EVENT_TASK_COMPLETED, self._on_hk_completed))
        on_unload(bus.async_listen(HK_EVENT_REGISTER_COMPANIONS, self._on_register_request))
        on_unload(bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_updated))
        on_unload(self._teardown)
        await self._register_companion()

        # after_dependencies orders setup, but the IPP sensors and Home Keeper's
        # services may still be on their way: reconcile once Home Assistant runs.
        if self.hass.is_running:
            await self.async_reconcile()
        else:
            self._cancel_started = bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._on_started
            )

    @callback
    def _teardown(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        if self._cancel_debounce is not None:
            self._cancel_debounce()
            self._cancel_debounce = None
        if self._cancel_catch_up is not None:
            self._cancel_catch_up()
            self._cancel_catch_up = None
        # async_listen_once removes itself when it fires; only cancel an unfired one.
        if self._cancel_started is not None:
            self._cancel_started()
            self._cancel_started = None

    async def _on_started(self, _event: Event) -> None:
        self._cancel_started = None
        await self.async_reconcile()

    async def _on_register_request(self, _event: Event) -> None:
        """Home Keeper is setting up: announce ourselves, catch up once it is loaded.

        The ping comes from inside Home Keeper's setup, while its entry does not yet
        count as loaded, so the catch-up usually waits a moment (see _catch_up).
        """
        await self._register_companion()
        if self._hk_loaded():
            await self.async_reconcile()
        else:
            self._schedule_catch_up(reset=True)

    async def _register_companion(self) -> None:
        if not self._hk_ready(HK_SERVICE_REGISTER_COMPANION):
            return
        lang = logic.lang_of(self.hass.config.language)
        try:
            await self.hass.services.async_call(
                HK_DOMAIN,
                HK_SERVICE_REGISTER_COMPANION,
                {
                    "domain": DOMAIN,
                    "name": "IPP",
                    "icon": COMPANION_ICON,
                    "description": TEXT[lang]["description"],
                    "config_entry_id": self.entry.entry_id,
                    "docs_url": DOCS_URL,
                    "capabilities": ["consumable_levels"],
                },
                blocking=False,
            )
        except Exception:
            _LOGGER.debug("Home Keeper companion registration failed", exc_info=True)

    # ── registry → markers ───────────────────────────────────────────────────
    def scan_markers(self) -> list[logic.Marker]:
        """Every marker in the registry that is not excluded, with its level.

        A disabled sensor is included as *paused*: disabling an IPP printer entry
        disables its device and every one of its entities (``disabled_by`` is
        ``config_entry``), and a user can disable one device or one sensor too. All of
        those pause the task rather than delete it. Only a sensor that leaves the
        registry — its printer entry was deleted — or one excluded in the options
        loses its task.
        """
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        excluded = self._excluded
        markers: list[logic.Marker] = []
        for entry in ent_reg.entities.values():
            if not is_marker_entry(entry) or entry.entity_id in excluded:
                continue
            paused = entry.disabled_by is not None
            device = dev_reg.async_get(entry.device_id) if entry.device_id else None
            printer = (device.name_by_user or device.name) if device else None
            state = self.hass.states.get(entry.entity_id)
            attrs = state.attributes if state else {}
            name = (
                entry.name
                or entry.original_name
                or (attrs.get("friendly_name") if state else None)
                or entry.entity_id
            )
            markers.append(
                logic.Marker(
                    uid=entry.unique_id,
                    entity_id=entry.entity_id,
                    device_id=entry.device_id,
                    printer=printer or "Printer",
                    name=str(name),
                    marker_type=attrs.get(IPP_ATTR_MARKER_TYPE),
                    low_level=logic.as_level(attrs.get(IPP_ATTR_MARKER_LOW_LEVEL)),
                    level=logic.as_level(state.state) if state and not paused else None,
                    paused=paused,
                )
            )
        self._markers = {m.entity_id: m for m in markers}
        self._follow_states()
        return markers

    def _follow_states(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        active = [eid for eid, m in self._markers.items() if not m.paused]
        if active:
            self._unsub_state = async_track_state_change_event(self.hass, active, self._on_state)

    @callback
    def _on_registry_updated(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        relevant = entity_id in self._markers or event.data.get("old_entity_id") in self._markers
        if not relevant and event.data.get("action") != "remove":
            entry = er.async_get(self.hass).async_get(entity_id) if entity_id else None
            relevant = entry is not None and is_marker_entry(entry)
        if not relevant:
            return
        if self._cancel_debounce is not None:
            self._cancel_debounce()
        self._cancel_debounce = async_call_later(
            self.hass, _REGISTRY_DEBOUNCE_SECONDS, self._debounced_reconcile
        )

    async def _debounced_reconcile(self, _now: datetime) -> None:
        self._cancel_debounce = None
        await self.async_reconcile()

    # ── state → task ─────────────────────────────────────────────────────────
    async def _on_state(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        marker = self._markers.get(entity_id)
        new_state = event.data.get("new_state")
        if marker is None or marker.paused or new_state is None:
            return
        attrs = new_state.attributes
        marker = replace(
            logic.with_level(marker, new_state.state),
            marker_type=attrs.get(IPP_ATTR_MARKER_TYPE) or marker.marker_type,
            # The printer's low mark comes with every reading; a cartridge of another
            # make can carry another one, so it is read fresh each time.
            low_level=logic.as_level(attrs.get(IPP_ATTR_MARKER_LOW_LEVEL)),
        )
        self._markers[entity_id] = marker
        if not self._hk_loaded():
            self._note_down()  # the reading is kept; the catch-up pass uses it
            return
        async with self._lock:
            try:
                tasks = await self._list_tasks()
                action = logic.plan_marker(tasks, marker, self.settings)
                if action is None:
                    return
                await self._execute(action)
                if isinstance(action, logic.CreateTask):
                    await self._reconcile_stock(list(self._markers.values()))
            except HomeKeeperUnavailable:
                return

    async def _on_hk_completed(self, event: Event) -> None:
        """Undo a completion the printer did not report (the task is the printer's)."""
        data = event.data
        if data.get("origin") == ORIGIN:
            return  # the echo of our own clear
        src = (data.get("source") or {}).get(SOURCE_NS)
        if not isinstance(src, dict) or not data.get("task_id"):
            return
        uid = src.get("marker_uid")
        marker = next((m for m in self._markers.values() if m.uid == uid), None)
        action = logic.plan_user_completion(
            data["task_id"], data.get("completed_at"), marker, self.settings
        )
        if action is None:
            return
        async with self._lock:
            try:
                await self._execute(action)
            except HomeKeeperUnavailable:
                return

    # ── reconcile ────────────────────────────────────────────────────────────
    async def async_reconcile(self) -> None:
        """Converge every task (and the spare-cartridge appliances) with the printers."""
        if not self._hk_ready(HK_SERVICE_LIST_TASKS):
            self.scan_markers()  # still follow the sensors; Home Keeper pings us later
            if self.hass.services.has_service(HK_DOMAIN, HK_SERVICE_LIST_TASKS):
                self._note_down()
            return
        async with self._lock:
            markers = self.scan_markers()
            try:
                tasks = await self._list_tasks()
                for action in logic.plan_reconcile(tasks, markers, self.settings):
                    await self._execute(action)
                await self._reconcile_stock(markers)
            except HomeKeeperUnavailable:
                return
            self._note_up()

    async def _reconcile_stock(self, markers: list[logic.Marker]) -> None:
        """Converge the spare-cartridge appliances; may raise :class:`HomeKeeperUnavailable`."""
        if not self._hk_ready(HK_SERVICE_LIST_ASSETS):
            return
        # Two passes at most: a new appliance gets its part ids on creation, and the
        # second pass links the tasks to them.
        for _ in range(2):
            assets = await self._list_assets()
            tasks = await self._list_tasks()
            actions = logic.plan_stock(
                assets, tasks, markers, self.settings, enabled=self._stock_enabled
            )
            for action in actions:
                await self._execute(action)
            if not any(isinstance(a, logic.EnsureAsset) for a in actions):
                return

    # ── Home Keeper calls ────────────────────────────────────────────────────
    def _hk_loaded(self) -> bool:
        """Whether a Home Keeper entry is loaded (its services alone don't say)."""
        return any(
            entry.state is ConfigEntryState.LOADED
            for entry in self.hass.config_entries.async_entries(HK_DOMAIN)
        )

    def _hk_ready(self, service: str) -> bool:
        return self._hk_loaded() and self.hass.services.has_service(HK_DOMAIN, service)

    def _note_down(self) -> None:
        if not self._hk_down:
            self._hk_down = True
            _LOGGER.info(
                "Home Keeper is not loaded yet (starting or reloading); cartridge "
                "tasks catch up once it is"
            )
        self._schedule_catch_up()

    @callback
    def _schedule_catch_up(self, *, reset: bool = False) -> None:
        """Try the full reconcile again shortly, until Home Keeper is loaded."""
        if reset:
            self._catch_up_attempts = 0
        if self._cancel_catch_up is not None:
            return  # one is already pending
        if self._catch_up_attempts >= _CATCH_UP_ATTEMPTS:
            return  # give up quietly; the next ping, reading or reload tries again
        self._catch_up_attempts += 1
        self._cancel_catch_up = async_call_later(self.hass, _CATCH_UP_SECONDS, self._catch_up)

    async def _catch_up(self, _now: datetime) -> None:
        self._cancel_catch_up = None
        if not self._hk_loaded():
            self._schedule_catch_up()
            return
        await self._register_companion()
        await self.async_reconcile()

    def _note_up(self) -> None:
        self._catch_up_attempts = 0
        if self._hk_down:
            self._hk_down = False
            _LOGGER.info("Home Keeper is loaded again; cartridge tasks caught up")

    async def _call(
        self,
        service: str,
        data: dict[str, Any],
        *,
        response: bool = False,
        required: bool = False,
    ) -> Any:
        """Call a Home Keeper service.

        Home Keeper going away mid-pass raises :class:`HomeKeeperUnavailable`, which
        stops the pass. Any other refusal is logged and skipped, so one bad call
        does not stop the rest — unless *required*: a read the pass depends on.
        """
        if not self._hk_ready(service):
            self._note_down()
            raise HomeKeeperUnavailable(service)
        try:
            return await self.hass.services.async_call(
                HK_DOMAIN, service, data, blocking=True, return_response=response
            )
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None) == "integration_not_loaded":
                self._note_down()
                raise HomeKeeperUnavailable(service) from err
            if required:
                _LOGGER.warning("home_keeper.%s failed: %s", service, err)
                raise HomeKeeperUnavailable(service) from err
            _LOGGER.warning("home_keeper.%s failed: %s", service, err)
            return None
        except (ValueError, KeyError) as err:
            _LOGGER.warning("home_keeper.%s failed: %s", service, err)
            if required:
                raise HomeKeeperUnavailable(service) from err
            return None

    async def _list_tasks(self) -> list[dict[str, Any]]:
        resp = await self._call(HK_SERVICE_LIST_TASKS, {}, response=True, required=True)
        if not isinstance(resp, dict) or not isinstance(resp.get("tasks"), list):
            raise HomeKeeperUnavailable(HK_SERVICE_LIST_TASKS)
        return list(resp["tasks"])

    async def _list_assets(self) -> list[dict[str, Any]]:
        resp = await self._call(HK_SERVICE_LIST_ASSETS, {}, response=True, required=True)
        if not isinstance(resp, dict) or not isinstance(resp.get("assets"), list):
            raise HomeKeeperUnavailable(HK_SERVICE_LIST_ASSETS)
        return list(resp["assets"])

    async def _execute(self, action: logic.Action | logic.StockAction) -> None:
        if isinstance(action, logic.CreateTask):
            await self._call(HK_SERVICE_ADD_TASK, action.payload, response=True)
            _LOGGER.debug("Created cartridge task for %s", action.uid)
        elif isinstance(action, logic.ArmTask):
            await self._call(HK_SERVICE_TRIGGER_TASK, {"task_id": action.task_id})
            await self._call(
                HK_SERVICE_UPDATE_TASK, {"task_id": action.task_id, "notes": action.notes}
            )
            _LOGGER.debug("Armed cartridge task %s", action.task_id)
        elif isinstance(action, logic.ClearTask):
            await self._call(
                HK_SERVICE_COMPLETE_TASK, {"task_id": action.task_id, "origin": ORIGIN}
            )
            _LOGGER.debug("Cleared cartridge task %s (new cartridge)", action.task_id)
        elif isinstance(action, logic.RevertCompletion):
            await self._revert_completion(action)
        elif isinstance(action, logic.DeleteTask):
            # force: our own tasks are deletion-protected while we are loaded.
            await self._call(HK_SERVICE_DELETE_TASK, {"task_id": action.task_id, "force": True})
            _LOGGER.debug("Deleted cartridge task %s", action.task_id)
        elif isinstance(action, (logic.PauseTask, logic.ResumeTask)):
            enabled = isinstance(action, logic.ResumeTask)
            await self._call(
                HK_SERVICE_UPDATE_TASK,
                {"task_id": action.task_id, "enabled": enabled, "source": action.source},
            )
            _LOGGER.debug(
                "%s cartridge task %s", "Resumed" if enabled else "Paused", action.task_id
            )
        elif isinstance(action, logic.EnsureAsset):
            await self._call(HK_SERVICE_ADD_ASSET, action.payload)
        elif isinstance(action, logic.UpdateManagedAsset):
            await self._call(
                HK_SERVICE_UPDATE_MANAGED_ASSET,
                {"asset_id": action.asset_id, "parts": action.parts},
            )
        elif isinstance(action, logic.LinkConsumable):
            await self._call(
                HK_SERVICE_SET_TASK_CONSUMABLE,
                {
                    "task_id": action.task_id,
                    "asset_id": action.asset_id,
                    "part_id": action.part_id,
                    "quantity": 1,
                },
            )
        elif isinstance(action, logic.UnlinkConsumable):
            await self._call(
                HK_SERVICE_SET_TASK_CONSUMABLE,
                {"task_id": action.task_id, "asset_id": None, "part_id": None},
            )

    async def _revert_completion(self, action: logic.RevertCompletion) -> None:
        """Remove the user's completion (stock comes back with it) and re-arm."""
        task = next((t for t in await self._list_tasks() if t.get("id") == action.task_id), None)
        if task is None:
            return
        ts = _completion_ts(task.get("completions") or [], action.completed_at)
        if ts is not None:
            await self._call(
                HK_SERVICE_DELETE_COMPLETION,
                {"task_id": action.task_id, "ts": ts, "origin": ORIGIN},
            )
        # Removing a completion leaves a triggered task's armed state alone.
        await self._call(HK_SERVICE_TRIGGER_TASK, {"task_id": action.task_id})
        _LOGGER.info(
            "Undid a manual completion of cartridge task %s: the printer has not "
            "reported a new cartridge",
            action.task_id,
        )


def _completion_ts(completions: list[dict[str, Any]], completed_at: str | None) -> str | None:
    """Return the ``ts`` of the completion an event announced, else the latest one."""
    entries = [c for c in completions if isinstance(c, dict) and c.get("ts")]
    if not entries:
        return None
    if completed_at:
        for entry in entries:
            if entry["ts"] == completed_at:
                return entry["ts"]
        want = _parse(completed_at)
        if want is not None:
            for entry in entries:
                if _parse(entry["ts"]) == want:
                    return entry["ts"]
    oldest = datetime.min.replace(tzinfo=UTC)
    return max(entries, key=lambda e: _parse(e["ts"]) or oldest)["ts"]


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    # A naive stamp is compared as UTC, so naive and aware never meet in max().
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
