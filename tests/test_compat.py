"""Keys that existing installations depend on must not change.

The glue keeps no storage of its own: it finds its tasks and appliances again through the
keys it writes into Home Keeper's ``source``, keyed on the IPP marker sensor's unique id.
Changing any of these, the domain or an option key would make an update lose the tasks
(and their replacement history) and the spare-cartridge counts.
"""

from __future__ import annotations

from custom_components.home_keeper_ipp import const, logic


def test_domain_and_namespaces() -> None:
    assert const.DOMAIN == "home_keeper_ipp"
    assert const.SOURCE_NS == "home_keeper_ipp"
    assert const.ORIGIN == "home_keeper_ipp"
    assert const.ASSET_ROLE == "printer_supplies"


def test_option_keys() -> None:
    assert {
        const.OPT_THRESHOLD,
        const.OPT_RECOVERY_MARGIN,
        const.OPT_CLEAR_ON_REFILL,
        const.OPT_NAME_TEMPLATE,
        const.OPT_EXCLUDED_ENTITIES,
        const.OPT_STOCK_ENABLED,
    } == {
        "threshold",
        "recovery_margin",
        "clear_on_refill",
        "name_template",
        "excluded_entities",
        "stock_enabled",
    }


def test_source_keys() -> None:
    marker = logic.Marker(
        uid="abc_marker_0", entity_id="sensor.x", device_id="dev", printer="P", name="Black"
    )
    settings = logic.Settings(config_entry_id="entry")
    task = logic.build_add_task_payload(marker, settings)
    assert task["source"] == {"home_keeper_ipp": {"marker_uid": "abc_marker_0", "device_id": "dev"}}
    asset = logic.build_asset_payload(marker.printer_key, [marker], settings)
    assert asset["source"] == {
        "home_keeper_ipp": {"role": "printer_supplies", "printer_key": "abc", "device_id": "dev"}
    }
    paused = logic.plan_reconcile(
        [{"id": "t", "enabled": True, "source": task["source"]}],
        [logic.Marker(**{**marker.__dict__, "paused": True})],
        settings,
    )[0]
    assert paused.source["home_keeper_ipp"]["paused"] is True
