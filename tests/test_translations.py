"""Translations are complete and have the same keys."""

import json
from pathlib import Path

from custom_components.home_keeper_ipp.const import MARKER_TYPE_LABELS, TEXT

BASE = Path(__file__).parents[1] / "custom_components" / "home_keeper_ipp"


def _keys(d, prefix=""):
    out = set()
    for k, v in d.items():
        path = f"{prefix}.{k}"
        out.add(path)
        if isinstance(v, dict):
            out |= _keys(v, path)
    return out


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_de_en_same_keys():
    de = _load(BASE / "translations" / "de.json")
    en = _load(BASE / "translations" / "en.json")
    assert _keys(de) == _keys(en)


def test_strings_json_is_en():
    assert _load(BASE / "strings.json") == _load(BASE / "translations" / "en.json")


def test_task_texts_complete():
    assert set(TEXT["de"]) == set(TEXT["en"])
    for labels in MARKER_TYPE_LABELS.values():
        assert {"en", "de"} <= set(labels)
