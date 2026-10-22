"""Unit tests for the pure decision logic."""

from __future__ import annotations

from custom_components.home_keeper_ipp import logic
from custom_components.home_keeper_ipp.const import HK_SOURCE_PART, SOURCE_NS

S = logic.Settings(config_entry_id="entry1", threshold=15, recovery_margin=20, lang="de")


def marker(level=None, *, uid="printer1_marker_0", name="Black ink", paused=False, **kw):
    return logic.Marker(
        uid=uid,
        entity_id=f"sensor.{uid}",
        device_id="dev1",
        printer="HP OfficeJet",
        name=name,
        marker_type=kw.pop("marker_type", "ink-cartridge"),
        level=level,
        paused=paused,
        **kw,
    )


def task(
    uid="printer1_marker_0",
    *,
    armed=False,
    enabled=True,
    paused=False,
    tid="t1",
    bound=None,
    **extra,
):
    src = {"marker_uid": uid, "device_id": "dev1"}
    if bound:
        src["marker_name"] = bound
    if paused:
        src["paused"] = True
    return {
        "id": tid,
        "next_due": "2026-10-11T08:00:00+00:00" if armed else None,
        "enabled": enabled,
        "source": {SOURCE_NS: src, **extra},
    }


# ── classification ───────────────────────────────────────────────────────────
def test_limits_and_classify():
    assert logic.limits(marker(50), S) == (15, 35)
    assert logic.classify(marker(15), S) == logic.LOW
    assert logic.classify(marker(16), S) == logic.BETWEEN
    assert logic.classify(marker(34), S) == logic.BETWEEN
    assert logic.classify(marker(35), S) == logic.OK
    assert logic.classify(marker(None), S) is None


def test_as_level_rejects_non_readings():
    for raw in ("unknown", "unavailable", None, "-2", "101", "nan"):
        assert logic.as_level(raw) is None
    assert logic.as_level("8") == 8


# ── task lifecycle ───────────────────────────────────────────────────────────
def test_low_without_task_creates_an_armed_task():
    action = logic.plan_marker([], marker(8), S)
    assert isinstance(action, logic.CreateTask)
    p = action.payload
    assert p["name"] == "Black ink tauschen (HP OfficeJet)"
    assert p["recurrence_type"] == "triggered"
    assert p["device_id"] == "dev1"
    assert p["source"][SOURCE_NS] == {"marker_uid": "printer1_marker_0", "device_id": "dev1"}
    assert p["task_chips"] == [{"label": "Tintenpatrone", "icon": "mdi:water"}]
    assert p["managed_by"]["deletion_protected"] is True
    assert "8 %" in p["notes"]
    assert "35 %" in p["notes"]


def test_low_rearms_a_dormant_task_and_leaves_an_armed_one():
    assert isinstance(logic.plan_marker([task()], marker(10), S), logic.ArmTask)
    assert logic.plan_marker([task(armed=True)], marker(10), S) is None


def test_refill_clears_an_armed_task_only():
    assert isinstance(logic.plan_marker([task(armed=True)], marker(100), S), logic.ClearTask)
    assert logic.plan_marker([task()], marker(100), S) is None
    off = logic.Settings(config_entry_id="e", clear_on_refill=False)
    assert logic.plan_marker([task(armed=True)], marker(100), off) is None


def test_between_and_unknown_change_nothing():
    for level in (20, None):
        assert logic.plan_marker([task(armed=True)], marker(level), S) is None
        assert logic.plan_marker([], marker(level), S) is None


def test_name_template_and_fallback():
    custom = logic.Settings(config_entry_id="e", name_template="Patrone {marker} @ {printer}")
    assert logic.task_name(marker(5), custom) == "Patrone Black ink @ HP OfficeJet"
    broken = logic.Settings(config_entry_id="e", name_template="{marker", lang="en")
    assert logic.task_name(marker(5), broken) == "Replace Black ink (HP OfficeJet)"


# ── manual completion is undone ──────────────────────────────────────────────
def test_user_completion_is_reverted_unless_a_new_cartridge_is_reported():
    revert = logic.plan_user_completion("t1", "ts", marker(10), S)
    assert revert == logic.RevertCompletion("t1", "printer1_marker_0", "ts")
    assert isinstance(
        logic.plan_user_completion("t1", "ts", marker(None), S), logic.RevertCompletion
    )
    assert logic.plan_user_completion("t1", "ts", marker(90), S) is None
    assert logic.plan_user_completion("t1", "ts", None, S) is None
    assert logic.plan_user_completion("t1", "ts", marker(10, paused=True), S) is None


# ── disabled printers / sensors ──────────────────────────────────────────────
def test_disabled_marker_pauses_its_task():
    actions = logic.plan_reconcile([task(armed=True)], [marker(None, paused=True)], S)
    assert len(actions) == 1
    assert isinstance(actions[0], logic.PauseTask)
    assert actions[0].source[SOURCE_NS]["paused"] is True


def test_paused_marker_never_creates_or_arms():
    assert logic.plan_reconcile([], [marker(5, paused=True)], S) == []
    assert (
        logic.plan_reconcile([task(enabled=False, paused=True)], [marker(5, paused=True)], S) == []
    )


def test_reenabled_marker_resumes_and_catches_up():
    actions = logic.plan_reconcile([task(enabled=False, paused=True)], [marker(5)], S)
    assert [type(a) for a in actions] == [logic.ResumeTask, logic.ArmTask]
    assert "paused" not in actions[0].source[SOURCE_NS]
    # A new cartridge went in while the printer was disabled: resume, then clear.
    actions = logic.plan_reconcile([task(armed=True, enabled=False, paused=True)], [marker(100)], S)
    assert [type(a) for a in actions] == [logic.ResumeTask, logic.ClearTask]


def test_task_the_user_switched_off_is_left_alone():
    user_off = task(enabled=False)
    assert logic.plan_reconcile([user_off], [marker(5)], S) == []
    assert logic.plan_reconcile([user_off], [marker(5, paused=True)], S) == []


def test_marker_gone_from_registry_deletes_the_task():
    actions = logic.plan_reconcile([task(tid="old", uid="gone_marker_0")], [], S)
    assert actions == [logic.DeleteTask("old", "gone_marker_0")]


# ── spare cartridges ─────────────────────────────────────────────────────────
def test_stock_creates_appliance_then_links_including_paused_markers():
    m0, m1 = marker(5), marker(None, uid="printer1_marker_1", name="Cyan ink", paused=True)
    first = logic.plan_stock([], [], [m0, m1], S, enabled=True)
    assert len(first) == 1
    assert isinstance(first[0], logic.EnsureAsset)
    names = [p["name"] for p in first[0].payload["parts"]]
    assert names == ["Black ink", "Cyan ink"]

    asset = {
        "id": "a1",
        "source": first[0].payload["source"],
        "parts": [
            {"id": "p0", "name": "Black ink", "type": "consumable", "notes": "Tintenpatrone"},
            {"id": "p1", "name": "Cyan ink", "type": "consumable", "notes": "Tintenpatrone"},
        ],
    }
    tasks = [task(), task(uid="printer1_marker_1", tid="t2", enabled=False, paused=True)]
    second = logic.plan_stock([asset], tasks, [m0, m1], S, enabled=True)
    assert second == [
        logic.LinkConsumable("t1", "a1", "p0"),
        logic.LinkConsumable("t2", "a1", "p1"),
    ]

    linked = [
        {**t, "source": {**t["source"], HK_SOURCE_PART: {"asset_id": "a1", "part_id": pid}}}
        for t, pid in zip(tasks, ("p0", "p1"), strict=True)
    ]
    assert logic.plan_stock([asset], linked, [m0, m1], S, enabled=True) == []
    assert logic.plan_stock([asset], linked, [m0, m1], S, enabled=False) == [
        logic.UnlinkConsumable("t1"),
        logic.UnlinkConsumable("t2"),
    ]


def test_counted_leftover_part_does_not_cause_rewrites():
    m0 = marker(50)
    asset = {
        "id": "a1",
        "source": {SOURCE_NS: {"role": "printer_supplies", "printer_key": "printer1"}},
        "parts": [
            {"id": "p0", "name": "Black ink", "type": "consumable", "notes": "Tintenpatrone"},
            {"id": "old", "name": "Old cartridge", "type": "consumable", "stock": 2},
        ],
    }
    assert logic.plan_stock([asset], [], [m0], S, enabled=True) == []
    asset["parts"][1]["stock"] = None
    actions = logic.plan_stock([asset], [], [m0], S, enabled=True)
    assert isinstance(actions[0], logic.UpdateManagedAsset)
    assert [p["name"] for p in actions[0].parts] == ["Black ink"]


# ── the printer's own low mark ───────────────────────────────────────────────
def test_printer_low_level_wins_when_usable():
    assert logic.limits(marker(50, low_level=10), S) == (10, 30)
    assert logic.classify(marker(12, low_level=10), S) == logic.BETWEEN
    assert logic.classify(marker(10, low_level=10), S) == logic.LOW
    assert logic.classify(marker(30, low_level=10), S) == logic.OK
    # A high printer mark caps recovery at 100 and stays above the low limit.
    assert logic.limits(marker(50, low_level=90), S) == (90, 100)


def test_unusable_printer_low_level_falls_back_to_threshold():
    for raw in (None, 0, -1, -2, 100, "unknown"):
        assert logic.limits(marker(50, low_level=raw), S) == (15, 35), raw


def test_notes_name_where_the_limit_came_from():
    notes = logic.task_notes(marker(8, low_level=10), S)
    assert "Untergrenze 10 %, vom Drucker" in notes
    assert "30 %" in notes
    notes = logic.task_notes(marker(8, low_level=0), S)
    assert "Untergrenze 15 %, aus den Optionen" in notes


# ── stable binding: the IPP integration numbers markers by sorted name ───────
def named(level, index, name, **kw):
    return marker(level, uid=f"printer1_marker_{index}", name=name, printer_name=name, **kw)


def test_new_task_is_bound_to_the_printers_name():
    payload = logic.plan_marker([], named(5, 0, "Black ink"), S).payload
    assert payload["source"][SOURCE_NS]["marker_name"] == "Black ink"


def test_display_rename_keeps_binding_and_part():
    renamed = marker(5, name="Schwarz (Büro)", printer_name="Black ink")
    assert logic.matches(task(bound="Black ink"), renamed)
    assert logic.desired_parts([renamed], "de")[0]["name"] == "Black ink"


def test_shifted_numbers_rebind_tasks_to_their_cartridges():
    # Before: Black ink = marker_0, Cyan ink = marker_1. The printer now also reports
    # "A waste box", which sorts first and pushes both one place down.
    tasks = [
        task("printer1_marker_0", tid="black", armed=True, bound="Black ink"),
        task("printer1_marker_1", tid="cyan", bound="Cyan ink"),
    ]
    markers = [named(90, 0, "A waste box"), named(8, 1, "Black ink"), named(100, 2, "Cyan ink")]
    actions = logic.plan_reconcile(tasks, markers, S)
    rebinds = {a.task_id: a.uid for a in actions if isinstance(a, logic.RebindTask)}
    assert rebinds == {"black": "printer1_marker_1", "cyan": "printer1_marker_2"}
    assert not [a for a in actions if isinstance(a, logic.DeleteTask)]
    # Black stays armed at 8 %, cyan at 100 % is fine, the waste box is not low:
    # nothing is created, cleared or armed by mistake.
    assert [a for a in actions if not isinstance(a, logic.RebindTask)] == []


def test_swapped_numbers_rebind_both_tasks():
    tasks = [
        task("printer1_marker_0", tid="black", armed=True, bound="Black ink"),
        task("printer1_marker_1", tid="cyan", bound="Cyan ink"),
    ]
    markers = [named(100, 0, "Cyan ink"), named(8, 1, "Black ink")]
    actions = logic.plan_reconcile(tasks, markers, S)
    rebinds = {a.task_id: a.uid for a in actions if isinstance(a, logic.RebindTask)}
    assert rebinds == {"black": "printer1_marker_1", "cyan": "printer1_marker_0"}
    assert len(actions) == 2


def test_cartridge_the_printer_no_longer_reports_loses_its_task():
    tasks = [task("printer1_marker_0", tid="black", bound="Black ink")]
    actions = logic.plan_reconcile(tasks, [named(50, 0, "Black Cartridge XL")], S)
    assert actions == [logic.DeleteTask("black", "printer1_marker_0")]


def test_task_from_before_names_is_backfilled_where_it_is():
    actions = logic.plan_reconcile([task(armed=True)], [named(8, 0, "Black ink")], S)
    assert len(actions) == 1
    assert isinstance(actions[0], logic.RebindTask)
    assert actions[0].uid == "printer1_marker_0"
    assert actions[0].source[SOURCE_NS]["marker_name"] == "Black ink"


def test_stale_binding_never_acts_on_the_wrong_cartridge():
    black_task = task("printer1_marker_0", armed=True, bound="Black ink")
    now_cyan = named(100, 0, "Cyan ink")
    assert logic.plan_marker([black_task], now_cyan, S) is None
    assert (
        logic.plan_user_completion("t1", "ts", named(5, 0, "Cyan ink"), S, task=black_task) is None
    )


def test_same_name_twice_on_one_printer_stays_on_its_sensor():
    tasks = [task("printer1_marker_1", tid="b", armed=True, bound="Black")]
    markers = [named(8, 0, "Black"), named(8, 1, "Black")]
    assert [
        a for a in logic.plan_reconcile(tasks, markers, S) if not isinstance(a, logic.CreateTask)
    ] == []


def test_sensor_without_printer_name_trusts_its_id():
    tasks = [task("printer1_marker_0", armed=True, bound="Black ink")]
    assert logic.plan_reconcile(tasks, [marker(8, printer_name=None)], S) == []


def test_part_named_after_display_name_is_renamed_not_replaced():
    renamed = marker(50, name="Schwarz", printer_name="Black ink")
    asset = {
        "id": "a1",
        "source": {SOURCE_NS: {"role": "printer_supplies", "printer_key": "printer1"}},
        "parts": [{"id": "p0", "name": "Schwarz", "type": "consumable", "stock": 2}],
    }
    actions = logic.plan_stock([asset], [], [renamed], S, enabled=True)
    assert len(actions) == 1
    assert actions[0].parts[0]["id"] == "p0"
    assert actions[0].parts[0]["name"] == "Black ink"


def test_live_reading_never_duplicates_a_task_bound_elsewhere():
    # The black ink's task still sits on marker_0; marker_1 now reports the black ink
    # at 8 %. Until the reconcile rebinds, the live path must not create a second task.
    tasks = [task("printer1_marker_0", armed=True, bound="Black ink")]
    assert logic.plan_marker(tasks, named(8, 1, "Black ink"), S) is None
    # Another printer's black ink is a different cartridge.
    other = marker(8, uid="printer2_marker_0", printer_name="Black ink")
    assert isinstance(logic.plan_marker(tasks, other, S), logic.CreateTask)
