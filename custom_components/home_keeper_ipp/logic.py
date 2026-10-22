"""Pure decision logic for the IPP glue.

Nothing here touches Home Assistant. The wiring reads the registries and states into
:class:`Marker` records, hands them in with Home Keeper's task and appliance lists,
and executes the actions that come back. Keeping the decisions pure is what lets
them be tested without a Home Assistant runtime.

The model, per marker (one ink or toner sensor of an IPP printer). The low limit is
the printer's own ``marker-low-levels`` value for that cartridge; when the printer
reports none (0, a negative "unknown", or nothing), the fixed threshold applies.

* level at or below the **low limit** → the task is armed (created on first sight,
  also when the cartridge is already low the moment the glue starts).
* level at or above the **recovery limit** (low limit + margin) → a new cartridge is
  in; an armed task is cleared, which records the replacement and draws a spare.
* in between → nothing changes, so a level wobbling near the line stays quiet.

A marker whose sensor is disabled — the whole IPP printer entry, its device, or just
that entity — is *paused*: its task is switched off (kept with its history, gone from
the to-do list and calendar) and marked as paused by us. When the sensor comes back
the task is switched on again and caught up with the level. A task the user switched
off is theirs and never touched. A marker that left the registry (the printer entry
was deleted) or was excluded in the options loses its task.

The task is the printer's to clear. A user who ticks it off in Home Keeper while the
printer does not report a fresh cartridge gets the completion undone and the task
re-armed — Home Keeper gives a spare drawn by that completion back to the stock.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any

from .const import (
    ASSET_ICON,
    ASSET_LOCKED_FIELDS,
    ASSET_ROLE,
    CHIP_ICON_INK,
    CHIP_ICON_OTHER,
    CHIP_ICON_TONER,
    HK_SOURCE_PART,
    IPP_MARKER_UNIQUE_ID_RE,
    LOCKED_FIELDS,
    MANAGED_DISPLAY_NAME,
    MANAGED_ICON,
    MARKER_TYPE_LABELS,
    PART_TYPE_CONSUMABLE,
    SOURCE_NS,
    TEXT,
)

LOW = "low"
OK = "ok"
BETWEEN = "between"


# ── inputs ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Marker:
    """One IPP marker sensor, as the wiring read it from the registries and state."""

    uid: str  # entity registry unique_id — stable across renames and restarts
    entity_id: str
    device_id: str | None
    printer: str
    name: str
    marker_type: str | None = None
    level: float | None = None  # None: unknown/unavailable
    low_level: float | None = None  # the printer's own low mark for this cartridge
    paused: bool = False  # the sensor is disabled (printer entry, device or entity)

    @property
    def printer_key(self) -> str:
        """The printer's own identity: the marker unique id without ``_marker_N``."""
        return re.sub(IPP_MARKER_UNIQUE_ID_RE, "", self.uid) or self.uid


@dataclass(frozen=True)
class Settings:
    """The options, resolved."""

    config_entry_id: str
    threshold: float = 15
    recovery_margin: float = 20
    clear_on_refill: bool = True
    name_template: str = ""
    lang: str = "en"


# ── actions ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CreateTask:
    """``add_task``: a new cartridge task, born armed."""

    uid: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ArmTask:
    """``trigger_task``, then ``update_task`` with notes naming the current level."""

    task_id: str
    uid: str
    notes: str


@dataclass(frozen=True)
class ClearTask:
    """``complete_task`` with our origin: a new cartridge was reported."""

    task_id: str
    uid: str


@dataclass(frozen=True)
class RevertCompletion:
    """``delete_completion`` for the user's completion, then ``trigger_task``."""

    task_id: str
    uid: str
    completed_at: str | None


@dataclass(frozen=True)
class DeleteTask:
    """``delete_task``: the marker is gone or excluded."""

    task_id: str
    uid: str


@dataclass(frozen=True)
class PauseTask:
    """``update_task`` with ``enabled: false`` and our ``paused`` marker."""

    task_id: str
    uid: str
    source: dict[str, Any]


@dataclass(frozen=True)
class ResumeTask:
    """``update_task`` with ``enabled: true``, our ``paused`` marker removed."""

    task_id: str
    uid: str
    source: dict[str, Any]


Action = CreateTask | ArmTask | ClearTask | RevertCompletion | DeleteTask | PauseTask | ResumeTask


@dataclass(frozen=True)
class EnsureAsset:
    """``add_asset``: the spare-cartridge appliance of a printer."""

    printer_key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class UpdateManagedAsset:
    """``update_managed_asset``: the part list follows the printer's markers."""

    asset_id: str
    parts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class LinkConsumable:
    """``set_task_consumable``: a cartridge task draws one spare per completion."""

    task_id: str
    asset_id: str
    part_id: str


@dataclass(frozen=True)
class UnlinkConsumable:
    """``set_task_consumable`` without a part: spare counting was turned off."""

    task_id: str


StockAction = EnsureAsset | UpdateManagedAsset | LinkConsumable | UnlinkConsumable


# ── small helpers ────────────────────────────────────────────────────────────
def lang_of(language: str | None) -> str:
    """Return the text table for a Home Assistant language code."""
    base = (language or "en").split("-")[0].split("_")[0].lower()
    return base if base in TEXT else "en"


def _t(lang: str, key: str) -> str:
    return TEXT.get(lang, TEXT["en"]).get(key) or TEXT["en"][key]


def as_level(value: Any) -> float | None:
    """Read a state as a percentage, or ``None`` when it is not one.

    IPP reports a negative level for "unknown" / "some remaining", which Home
    Assistant turns into ``None``; ``unknown`` and ``unavailable`` are states too.
    None of those is a reading to act on — an offline printer is not a refilled one.
    """
    try:
        level = float(value)
    except TypeError, ValueError:
        return None
    if level != level or level < 0 or level > 100:  # NaN, or out of range
        return None
    return level


def _fmt(level: float | None) -> str:
    if level is None:
        return "?"
    return str(int(level)) if float(level).is_integer() else f"{level:.1f}"


def printer_low(marker: Marker) -> float | None:
    """Return the printer's low mark when it is a usable percentage, else ``None``."""
    low = as_level(marker.low_level)
    return low if low is not None and 0 < low < 100 else None


def limits(marker: Marker, settings: Settings) -> tuple[float, float]:
    """Return the (low, recovery) limits for *marker*.

    Low is the printer's own mark, or the fixed threshold when it reports none.
    Recovery is low + margin, capped at 100 and always above low.
    """
    own = printer_low(marker)
    low = own if own is not None else float(settings.threshold)
    recover = min(100.0, low + max(1.0, float(settings.recovery_margin)))
    if recover <= low:
        recover = low + 1
    return low, recover


def classify(marker: Marker, settings: Settings) -> str | None:
    """``LOW``, ``OK``, ``BETWEEN``, or ``None`` when the level is unknown."""
    if marker.level is None:
        return None
    low, recover = limits(marker, settings)
    if marker.level <= low:
        return LOW
    if marker.level >= recover:
        return OK
    return BETWEEN


# ── our tasks ────────────────────────────────────────────────────────────────
def ns(item: dict[str, Any] | None) -> dict[str, Any] | None:
    """Our namespace on a task or appliance ``source``, or ``None``."""
    if not isinstance(item, dict):
        return None
    src = (item.get("source") or {}).get(SOURCE_NS)
    return src if isinstance(src, dict) else None


def our_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the well-formed tasks that carry our ``source`` namespace."""
    return [t for t in tasks if isinstance(t, dict) and t.get("id") and ns(t)]


def task_for(tasks: list[dict[str, Any]], uid: str) -> dict[str, Any] | None:
    """Return our task for the marker *uid*, or ``None``."""
    for task in our_tasks(tasks):
        if (ns(task) or {}).get("marker_uid") == uid:
            return task
    return None


def is_armed(task: dict[str, Any]) -> bool:
    """Tell whether a triggered task is armed (it has a ``next_due``)."""
    return bool(task.get("next_due"))


def is_enabled(task: dict[str, Any]) -> bool:
    """Tell whether the task is switched on."""
    return task.get("enabled", True) is not False


def paused_by_us(task: dict[str, Any]) -> bool:
    """Tell whether we switched the task off (and so may switch it on again)."""
    return bool((ns(task) or {}).get("paused"))


def _ns_with(task: dict[str, Any], marker: Marker, *, paused: bool) -> dict[str, Any]:
    """Our namespace for *task*, with the paused marker set or removed."""
    data = {**(ns(task) or {}), "marker_uid": marker.uid, "device_id": marker.device_id}
    if paused:
        data["paused"] = True
    else:
        data.pop("paused", None)
    return {SOURCE_NS: data}


def marker_type_label(marker_type: str | None, lang: str) -> str | None:
    """Return a readable label for an IPP ``marker-types`` keyword."""
    if not marker_type:
        return None
    labels = MARKER_TYPE_LABELS.get(marker_type)
    if labels is None:
        return marker_type
    return labels.get(lang) or labels["en"]


def _chip(marker: Marker, lang: str) -> dict[str, str] | None:
    label = marker_type_label(marker.marker_type, lang)
    if not label:
        return None
    kind = (marker.marker_type or "").lower()
    if "ink" in kind:
        icon = CHIP_ICON_INK
    elif "toner" in kind:
        icon = CHIP_ICON_TONER
    else:
        icon = CHIP_ICON_OTHER
    return {"label": label, "icon": icon}


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def task_name(marker: Marker, settings: Settings) -> str:
    """Render the name template; a broken template falls back to the default."""
    default = _t(settings.lang, "name_template")
    template = (settings.name_template or "").strip() or default
    values = _Blank(marker=marker.name, printer=marker.printer)
    try:
        name = template.format_map(values).strip()
    except ValueError, IndexError, AttributeError, KeyError:
        name = ""
    return name or default.format_map(values)


def task_notes(marker: Marker, settings: Settings) -> str:
    """Markdown notes: printer, cartridge, type, and the level that armed the task."""
    lang = settings.lang
    low, recover = limits(marker, settings)
    lines = [
        f"**{_t(lang, 'printer')}:** {marker.printer}",
        f"**{_t(lang, 'marker')}:** {marker.name}",
    ]
    label = marker_type_label(marker.marker_type, lang)
    if label:
        lines.append(f"**{_t(lang, 'type')}:** {label}")
    lines.append(
        f"**{_t(lang, 'level')}:** {_fmt(marker.level)} % "
        f"({_t(lang, 'limit')} {_fmt(low)} %, "
        f"{_t(lang, 'from_printer' if printer_low(marker) is not None else 'from_options')})"
    )
    if marker.level is not None and marker.level <= 0:
        lines += ["", _t(lang, "empty")]
    lines += ["", _t(lang, "clears").format(recover=_fmt(recover))]
    return "\n".join(lines)


def build_add_task_payload(marker: Marker, settings: Settings) -> dict[str, Any]:
    """Build the ``home_keeper.add_task`` payload for a new (armed) cartridge task."""
    chip = _chip(marker, settings.lang)
    payload: dict[str, Any] = {
        "name": task_name(marker, settings),
        "notes": task_notes(marker, settings),
        "recurrence_type": "triggered",
        # device_id at the top of our namespace: Home Keeper heals exactly that key
        # when Home Assistant renumbers a device (INTEGRATING.md §1).
        "source": {SOURCE_NS: {"marker_uid": marker.uid, "device_id": marker.device_id}},
        "task_chips": [chip] if chip else [],
        "managed_by": {
            "integration": SOURCE_NS,
            "display_name": MANAGED_DISPLAY_NAME,
            "icon": MANAGED_ICON,
            "config_entry_id": settings.config_entry_id,
            "deletion_protected": True,
            "completion_prompt": _t(settings.lang, "completion_prompt"),
            "locked_fields": list(LOCKED_FIELDS),
        },
    }
    if marker.device_id:
        payload["device_id"] = marker.device_id
    return payload


# ── planners ─────────────────────────────────────────────────────────────────
def plan_marker(tasks: list[dict[str, Any]], marker: Marker, settings: Settings) -> Action | None:
    """Decide what one marker's current level means for its task.

    A paused marker, and a task that is switched off, are left alone here; pausing
    and resuming is :func:`plan_reconcile`'s job.
    """
    if marker.paused:
        return None
    state = classify(marker, settings)
    if state is None or state == BETWEEN:
        return None
    task = task_for(tasks, marker.uid)
    if task is not None and not is_enabled(task):
        return None
    if state == LOW:
        if task is None:
            return CreateTask(marker.uid, build_add_task_payload(marker, settings))
        if is_armed(task):
            return None
        return ArmTask(task["id"], marker.uid, task_notes(marker, settings))
    # OK: a fresh cartridge.
    if task is not None and is_armed(task) and settings.clear_on_refill:
        return ClearTask(task["id"], marker.uid)
    return None


def plan_user_completion(
    task_id: str,
    completed_at: str | None,
    marker: Marker | None,
    settings: Settings,
) -> RevertCompletion | None:
    """Decide what to do when a user ticked our task off in Home Keeper.

    Only the printer may clear the task. Unless it reports a fresh cartridge right now
    (which the glue would clear on its own anyway), the completion is undone and the
    task armed again. A marker the glue no longer tracks is left to the user.

    With "clear on refill" turned off the user *is* the one who clears the task, so a
    completion then stands once the printer reports the new cartridge.
    """
    if marker is None or marker.paused:
        return None
    if classify(marker, settings) == OK:
        return None
    return RevertCompletion(task_id, marker.uid, completed_at)


def plan_reconcile(
    tasks: list[dict[str, Any]], markers: list[Marker], settings: Settings
) -> list[Action]:
    """Converge every marker's task with its current level; pause, resume, drop.

    *markers* holds every marker in the registry that is not excluded, paused ones
    included. A task whose marker is not among them (the printer entry was deleted,
    the sensor was excluded in the options) is deleted.
    """
    actions: list[Action] = []
    wanted = {m.uid for m in markers}
    for task in our_tasks(tasks):
        uid = (ns(task) or {}).get("marker_uid")
        if uid not in wanted:
            actions.append(DeleteTask(task["id"], str(uid)))
    view = list(tasks)
    for marker in markers:
        task = task_for(view, marker.uid)
        if marker.paused:
            if task is not None and is_enabled(task):
                actions.append(
                    PauseTask(task["id"], marker.uid, _ns_with(task, marker, paused=True))
                )
            continue
        if task is not None and not is_enabled(task) and paused_by_us(task):
            source = _ns_with(task, marker, paused=False)
            actions.append(ResumeTask(task["id"], marker.uid, source))
            # Plan the level against the task as it is once resumed.
            resumed = {**task, "enabled": True, "source": {**(task.get("source") or {}), **source}}
            view = [resumed if t is task else t for t in view]
        action = plan_marker(view, marker, settings)
        if action is not None:
            actions.append(action)
    return actions


# ── spare cartridges (one managed appliance per printer) ─────────────────────
def find_printer_asset(assets: list[dict[str, Any]], printer_key: str) -> dict[str, Any] | None:
    """Return our spare-cartridge appliance for the printer, or ``None``."""
    for asset in assets:
        src = ns(asset)
        if src and src.get("role") == ASSET_ROLE and src.get("printer_key") == printer_key:
            return asset
    return None


def our_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every spare-cartridge appliance we own."""
    return [a for a in assets if (ns(a) or {}).get("role") == ASSET_ROLE]


def part_names(markers: list[Marker]) -> dict[str, str]:
    """Uid → part name, made unique per printer (two markers may share a name)."""
    seen: dict[str, int] = {}
    out: dict[str, str] = {}
    for marker in markers:
        base = marker.name.strip() or marker.uid
        count = seen.get(base, 0)
        seen[base] = count + 1
        out[marker.uid] = base if count == 0 else f"{base} ({count + 1})"
    return out


def desired_parts(markers: list[Marker], lang: str) -> list[dict[str, Any]]:
    """Return the owner keys of one consumable part per marker."""
    names = part_names(markers)
    parts = []
    for marker in markers:
        part: dict[str, Any] = {"name": names[marker.uid], "type": PART_TYPE_CONSUMABLE}
        label = marker_type_label(marker.marker_type, lang)
        if label:
            part["notes"] = label
        parts.append(part)
    return parts


def build_asset_payload(
    printer_key: str, markers: list[Marker], settings: Settings
) -> dict[str, Any]:
    """Build the ``home_keeper.add_asset`` payload for a printer's spare cartridges."""
    first = markers[0]
    payload: dict[str, Any] = {
        "name": first.printer,
        "icon": ASSET_ICON,
        "notes": _t(settings.lang, "part_notes").format(printer=first.printer),
        "parts": desired_parts(markers, settings.lang),
        "source": {
            SOURCE_NS: {
                "role": ASSET_ROLE,
                "printer_key": printer_key,
                "device_id": first.device_id,
            }
        },
        "managed_by": {
            "integration": SOURCE_NS,
            "display_name": MANAGED_DISPLAY_NAME,
            "icon": MANAGED_ICON,
            "config_entry_id": settings.config_entry_id,
            "deletion_protected": True,
            "locked_fields": list(ASSET_LOCKED_FIELDS),
        },
    }
    if first.device_id:
        payload["device_id"] = first.device_id
    return payload


_OWNER_KEYS = ("name", "type", "notes")


def _part_by_name(asset: dict[str, Any], name: str) -> dict[str, Any] | None:
    for part in asset.get("parts") or []:
        if part.get("name") == name:
            return part
    return None


def _owner_view(part: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(part.get(k) or None for k in _OWNER_KEYS)


def plan_stock(
    assets: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    markers: list[Marker],
    settings: Settings,
    *,
    enabled: bool,
) -> list[StockAction]:
    """Keep one appliance of spare cartridges per printer; link each task to its part.

    The appliance is created on first sight and its part list follows the printer's
    markers. Each cartridge task draws one spare when it is completed, and Home Keeper
    opens a "Buy …" task when the user enabled that on a part with a reorder point.
    Turning the option off unlinks the tasks and leaves appliances and counts alone.
    """
    actions: list[StockAction] = []
    ours = {a.get("id") for a in our_assets(assets)}
    if not enabled:
        for task in our_tasks(tasks):
            link = (task.get("source") or {}).get(HK_SOURCE_PART)
            if isinstance(link, dict) and link.get("asset_id") in ours:
                actions.append(UnlinkConsumable(task["id"]))
        return actions

    groups: dict[str, list[Marker]] = {}
    for marker in markers:
        groups.setdefault(marker.printer_key, []).append(marker)

    for printer_key, group in groups.items():
        asset = find_printer_asset(assets, printer_key)
        if asset is None:
            actions.append(
                EnsureAsset(printer_key, build_asset_payload(printer_key, group, settings))
            )
            continue  # linked on the next pass, once the appliance has an id
        wanted = desired_parts(group, settings.lang)
        merged: list[dict[str, Any]] = []
        for part in wanted:
            existing = _part_by_name(asset, part["name"])
            if existing and existing.get("id"):
                merged.append({**part, "id": existing["id"]})
            else:
                merged.append(dict(part))
        wanted_names = {p["name"] for p in wanted}
        # A part no marker uses any more is left out of the list. Home Keeper drops it
        # when it counts nothing and keeps it when the user counted spares, so only an
        # uncounted leftover is a reason to write (else every pass would rewrite).
        differs = any(
            _part_by_name(asset, p["name"]) is None
            or _owner_view(_part_by_name(asset, p["name"]) or {}) != _owner_view(p)
            for p in wanted
        ) or any(
            p.get("name") not in wanted_names and p.get("stock") is None
            for p in asset.get("parts") or []
        )
        if differs:
            actions.append(UpdateManagedAsset(asset["id"], merged))

        names = part_names(group)
        for marker in group:
            task = task_for(tasks, marker.uid)
            part = _part_by_name(asset, names[marker.uid])
            if task is None or part is None or not part.get("id"):
                continue
            link = (task.get("source") or {}).get(HK_SOURCE_PART)
            if not (
                isinstance(link, dict)
                and link.get("asset_id") == asset["id"]
                and link.get("part_id") == part["id"]
            ):
                actions.append(LinkConsumable(task["id"], asset["id"], part["id"]))
    return actions


def with_level(marker: Marker, level: Any) -> Marker:
    """Return a copy of *marker* with a new reading (used by the state listener)."""
    return replace(marker, level=as_level(level))
