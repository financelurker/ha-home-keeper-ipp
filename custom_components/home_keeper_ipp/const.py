"""Constants for the Home Keeper ↔ IPP glue integration.

The glue owns no schedule logic. It watches the ink and toner level sensors that
Home Assistant's built-in ``ipp`` integration creates (one per *marker*) and turns a
low level into a Home Keeper ``triggered`` task, cleared again when a fresh cartridge
is put in. It talks to both sides over the event bus, the registries and services
only — no Python import in either direction.
"""

from __future__ import annotations

DOMAIN = "home_keeper_ipp"

# ── Home Keeper side ─────────────────────────────────────────────────────────
HK_DOMAIN = "home_keeper"
HK_EVENT_TASK_COMPLETED = "home_keeper_task_completed"
HK_EVENT_REGISTER_COMPANIONS = "home_keeper_register_companions"
HK_SERVICE_REGISTER_COMPANION = "register_companion"
HK_SERVICE_LIST_TASKS = "list_tasks"
HK_SERVICE_ADD_TASK = "add_task"
HK_SERVICE_UPDATE_TASK = "update_task"
HK_SERVICE_DELETE_TASK = "delete_task"
HK_SERVICE_COMPLETE_TASK = "complete_task"
HK_SERVICE_TRIGGER_TASK = "trigger_task"
HK_SERVICE_LIST_ASSETS = "list_assets"
HK_SERVICE_ADD_ASSET = "add_asset"
HK_SERVICE_UPDATE_ASSET = "update_asset"
HK_SERVICE_DELETE_ASSET = "delete_asset"
HK_SERVICE_UPDATE_MANAGED_ASSET = "update_managed_asset"
HK_SERVICE_SET_TASK_CONSUMABLE = "set_task_consumable"
# Home Keeper owns the ``part`` namespace on a task's ``source`` (a consumable link).
HK_SOURCE_PART = "part"

# Our namespace in a task's (and an appliance's) ``source``, and the ``origin`` we
# send on completions we make ourselves, so our listener can drop the echo.
SOURCE_NS = DOMAIN
ORIGIN = DOMAIN

# ── IPP side (EXTERNAL CONTRACT — homeassistant/components/ipp/sensor.py) ────
# One sensor per marker: key ``marker_<index>``, ``translation_key="marker"``, the
# marker name as the entity name, the level in percent as its state (``None`` when
# the printer reports a negative "unknown" level), and these attributes.
IPP_DOMAIN = "ipp"
IPP_MARKER_TRANSLATION_KEY = "marker"
IPP_MARKER_UNIQUE_ID_RE = r"_marker_\d+$"
IPP_ATTR_MARKER_TYPE = "marker_type"
IPP_ATTR_MARKER_LOW_LEVEL = "marker_low_level"
IPP_ATTR_MARKER_HIGH_LEVEL = "marker_high_level"

# ── Options ──────────────────────────────────────────────────────────────────
OPT_THRESHOLD = "threshold"
OPT_RECOVERY_MARGIN = "recovery_margin"
OPT_CLEAR_ON_REFILL = "clear_on_refill"
OPT_NAME_TEMPLATE = "name_template"
OPT_EXCLUDED_ENTITIES = "excluded_entities"
OPT_STOCK_ENABLED = "stock_enabled"

DEFAULT_THRESHOLD = 15
# A new cartridge reads far above the low line, while a level jitters by a point or
# two. The task clears only once the level is this many points above the low line,
# so a low cartridge wobbling around the line never clears and re-arms.
DEFAULT_RECOVERY_MARGIN = 20
DEFAULT_CLEAR_ON_REFILL = True
DEFAULT_STOCK_ENABLED = True
# Empty means "pick the template for the Home Assistant language".
DEFAULT_NAME_TEMPLATE = ""

# ── Managed ownership ────────────────────────────────────────────────────────
MANAGED_DISPLAY_NAME = "IPP printer"
MANAGED_ICON = "mdi:printer-alert"
COMPANION_ICON = "mdi:printer"
# Shown in Home Keeper's Settings → Companions. Point it at your own repository.
DOCS_URL = "https://github.com/financelurker/ha-home-keeper-ipp"
# The fields we write once and never change. ``notes`` and ``task_chips`` stay
# unlocked: the notes are rewritten on every arm with the level that armed the task,
# and Home Keeper strips locked fields from every ``update_task``, ours included.
LOCKED_FIELDS = ["name", "device_id", "recurrence_type"]

# The appliance we keep per printer for the spare cartridges.
ASSET_ROLE = "printer_supplies"
ASSET_LOCKED_FIELDS = ["parts"]
ASSET_ICON = "mdi:printer"
PART_TYPE_CONSUMABLE = "consumable"

# ── Text per language (task data, so it follows the Home Assistant language) ─
TEXT: dict[str, dict[str, str]] = {
    "en": {
        "name_template": "Replace {marker} ({printer})",
        "completion_prompt": (
            "Clears itself when a new cartridge is reported. A manual Done is undone."
        ),
        "printer": "Printer",
        "marker": "Cartridge",
        "type": "Type",
        "level": "Level when the task was raised",
        "limit": "low limit",
        "from_printer": "set by the printer",
        "from_options": "from the options",
        "clears": "The task clears by itself once the printer reports {recover} % or more.",
        "empty": "The cartridge is reported **empty**.",
        "part_notes": "Spare cartridges for {printer}.",
        "description": (
            "Turns low ink and toner levels from the IPP integration into "
            "“replace cartridge” tasks, cleared when a new cartridge goes in."
        ),
    },
    "de": {
        "name_template": "{marker} tauschen ({printer})",
        "completion_prompt": (
            "Erledigt sich beim Patronenwechsel selbst. Manuelles Abhaken wird zurückgenommen."
        ),
        "printer": "Drucker",
        "marker": "Patrone",
        "type": "Typ",
        "level": "Füllstand beim Auslösen",
        "limit": "Untergrenze",
        "from_printer": "vom Drucker",
        "from_options": "aus den Optionen",
        "clears": (
            "Der Task erledigt sich selbst, sobald der Drucker {recover} % oder mehr meldet."
        ),
        "empty": "Die Patrone ist **leer**.",
        "part_notes": "Ersatzpatronen für {printer}.",
        "description": (
            "Macht aus niedrigen Tinten- und Tonerständen der IPP-Integration "
            "„Patrone tauschen“-Tasks, die sich beim Patronenwechsel selbst erledigen."
        ),
    },
}

# A readable label and an icon per IPP ``marker-types`` keyword (RFC 8011 / PWG 5100).
MARKER_TYPE_LABELS: dict[str, dict[str, str]] = {
    "ink": {"en": "Ink", "de": "Tinte"},
    "ink-cartridge": {"en": "Ink cartridge", "de": "Tintenpatrone"},
    "inkCartridge": {"en": "Ink cartridge", "de": "Tintenpatrone"},
    "toner": {"en": "Toner", "de": "Toner"},
    "toner-cartridge": {"en": "Toner cartridge", "de": "Tonerkartusche"},
    "tonerCartridge": {"en": "Toner cartridge", "de": "Tonerkartusche"},
    "waste-ink": {"en": "Waste ink", "de": "Resttinte"},
    "waste-toner": {"en": "Waste toner", "de": "Resttoner"},
    "opc": {"en": "Drum (OPC)", "de": "Trommel (OPC)"},
    "developer": {"en": "Developer", "de": "Entwickler"},
    "fuser": {"en": "Fuser", "de": "Fixiereinheit"},
    "staples": {"en": "Staples", "de": "Heftklammern"},
}
CHIP_ICON_INK = "mdi:water"
CHIP_ICON_TONER = "mdi:printer-3d-nozzle"
CHIP_ICON_OTHER = "mdi:printer-settings"
