"""Consistency checks of the translation and icon files against the code."""

import ast
import json
from pathlib import Path
import string
from typing import Any

import pytest

from custom_components.hetzner.binary_sensor import (
    ACCESS_SETTINGS,
    OBJECT_ID_NAMES as BINARY_OBJECT_ID_NAMES,
    SUBACCOUNT_ACCESS_SETTINGS,
)
from custom_components.hetzner.const import STORAGE_BOX_STATUSES
from custom_components.hetzner.sensor import OBJECT_ID_NAMES, SIZE_SENSORS

COMPONENT = Path(__file__).parent.parent / "custom_components" / "hetzner"
LANGUAGES = ["de", "nl"]


def _load(path: Path) -> dict[str, Any]:
    """Load a JSON file of the integration."""
    with path.open(encoding="utf-8") as file:
        result: dict[str, Any] = json.load(file)
    return result


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Return all strings of a translation file keyed by their dotted path."""
    result: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            result.update(_flatten(value, f"{path}."))
        else:
            result[path] = value
    return result


def _placeholders(text: str) -> set[str]:
    """Return the placeholder names used in a translation string."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


EN = _load(COMPONENT / "translations" / "en.json")


def _code_translation_keys() -> dict[str, dict[str, set[str]]]:
    """Collect literal translation keys of the code by section.

    Returns {section: {key: placeholder names passed with it}}; issues are
    created with async_create_issue, every other call except entity
    descriptions raises an exception.
    """
    found: dict[str, dict[str, set[str]]] = {"exceptions": {}, "issues": {}}
    for path in COMPONENT.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}
            key = keywords.get("translation_key")
            if not isinstance(key, ast.Constant):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id.endswith("EntityDescription"):
                # Entity names, checked by the entity translation tests
                continue
            name = func.attr if isinstance(func, ast.Attribute) else ""
            section = "issues" if name == "async_create_issue" else "exceptions"
            placeholders = keywords.get("translation_placeholders")
            names: set[str] = set()
            if isinstance(placeholders, ast.Dict):
                names = {
                    str(k.value)
                    for k in placeholders.keys
                    if isinstance(k, ast.Constant)
                }
            found[section][str(key.value)] = names
    return found


def _entity_translation_keys() -> set[str]:
    """Collect the literal _attr_translation_key values of the sensors."""
    tree = ast.parse((COMPONENT / "sensor.py").read_text(encoding="utf-8"))
    keys = {str(description.translation_key) for description in SIZE_SENSORS}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and any(
                isinstance(target, ast.Name) and target.id == "_attr_translation_key"
                for target in node.targets
            )
        ):
            keys.add(str(node.value.value))
    return keys


def _config_flow_error_keys() -> set[str]:
    """Collect the form error keys the config flow can show."""
    tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_error_key":
            keys.update(
                str(ret.value.value)
                for ret in ast.walk(node)
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Constant)
            )
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "errors"
                for target in node.targets
            )
        ):
            keys.add(str(node.value.value))
    return keys


def test_strings_match_english() -> None:
    """strings.json is the source of en.json, so both must be identical."""
    assert _load(COMPONENT / "strings.json") == EN


@pytest.mark.parametrize("language", LANGUAGES)
def test_languages_match_english(language: str) -> None:
    """Every language has the same keys and placeholders as English."""
    english = _flatten(EN)
    other = _flatten(_load(COMPONENT / "translations" / f"{language}.json"))

    assert set(other) == set(english)
    for key, text in english.items():
        assert _placeholders(other[key]) == _placeholders(text), key
        assert other[key].strip(), key


def test_code_translation_keys_exist() -> None:
    """Raised exceptions and repair issues have English texts and placeholders."""
    found = _code_translation_keys()
    assert found["exceptions"]
    assert found["issues"]

    for key, names in found["exceptions"].items():
        assert key in EN["exceptions"], key
        assert _placeholders(EN["exceptions"][key]["message"]) == names, key
    for key, names in found["issues"].items():
        issue = EN["issues"][key]
        assert _placeholders(issue["title"]) | _placeholders(issue["description"]) == (
            names
        )


def test_no_unused_exception_or_issue_translations() -> None:
    """Every exception and issue translation is used by the code."""
    found = _code_translation_keys()
    assert set(EN["exceptions"]) == set(found["exceptions"])
    assert set(EN["issues"]) == set(found["issues"])


def test_entity_translations() -> None:
    """Every sensor translation key is translated and has an icon."""
    keys = _entity_translation_keys()
    sensors = EN["entity"]["sensor"]

    assert set(sensors) == keys
    for key in keys - {"status"}:
        assert sensors[key]["name"], key
    # The status sensor takes the device name; only its states are translated
    assert "name" not in sensors["status"]
    assert set(sensors["status"]["state"]) == set(STORAGE_BOX_STATUSES)

    icons = _load(COMPONENT / "icons.json")["entity"]["sensor"]
    assert set(icons) == keys
    assert set(icons["status"]["state"]) <= set(STORAGE_BOX_STATUSES)


def test_object_id_names_match_english() -> None:
    """The English names behind the entity_ids are the English entity names."""
    sensors = EN["entity"]["sensor"]
    assert {
        key: value["name"] for key, value in sensors.items() if "name" in value
    } == OBJECT_ID_NAMES
    # Main account binary sensor names end with " (main account)"; the
    # entity_ids leave it out. Subaccount ones have their own device
    main_keys = {str(description.translation_key) for description in ACCESS_SETTINGS}
    binary_sensors = EN["entity"]["binary_sensor"]
    assert {
        key: f"{name} (main account)" if key in main_keys else name
        for key, name in BINARY_OBJECT_ID_NAMES.items()
    } == {key: value["name"] for key, value in binary_sensors.items()}
    for key in set(binary_sensors) - main_keys:
        assert "main account" not in binary_sensors[key]["name"], key


def test_binary_sensor_translations() -> None:
    """Every access setting binary sensor has a name and on/off icons."""
    keys = {
        str(description.translation_key)
        for description in ACCESS_SETTINGS + SUBACCOUNT_ACCESS_SETTINGS
    }
    # One per access setting the API documents for the box and for subaccounts
    assert {description.key for description in ACCESS_SETTINGS} == {
        "reachable_externally",
        "samba_enabled",
        "ssh_enabled",
        "webdav_enabled",
        "zfs_enabled",
    }
    assert {description.key for description in SUBACCOUNT_ACCESS_SETTINGS} == {
        "reachable_externally",
        "samba_enabled",
        "ssh_enabled",
        "webdav_enabled",
        "readonly",
    }
    # Main account names say "(main account)", so subaccounts need their own keys
    assert len(keys) == len(ACCESS_SETTINGS) + len(SUBACCOUNT_ACCESS_SETTINGS)

    binary_sensors = EN["entity"]["binary_sensor"]
    assert set(binary_sensors) == keys
    for key in keys:
        assert binary_sensors[key]["name"], key

    icons = _load(COMPONENT / "icons.json")["entity"]["binary_sensor"]
    assert set(icons) == keys
    for key in keys:
        assert icons[key]["default"].startswith("mdi:"), key
        assert set(icons[key]["state"]) == {"on", "off"}, key


def test_no_unit_setting_left() -> None:
    """The unit setting was removed together with the options flow."""
    assert "options" not in EN
    assert "selector" not in EN
    assert set(EN["config"]["step"]["user"]["data"]) == {"api_key"}
    assert set(EN["config"]["step"]["user"]["data_description"]) == {"api_key"}
    assert set(EN["entity"]) == {"binary_sensor", "sensor"}


def test_config_flow_errors_translated() -> None:
    """Every error the config flow can show has a text."""
    keys = _config_flow_error_keys()

    assert "no_storage_boxes" in keys
    assert "api_error" in keys
    assert keys <= set(EN["config"]["error"])


@pytest.mark.parametrize(
    ("language", "readonly"),
    [("en", "Read-only"), ("de", "Schreibgeschützt"), ("nl", "Alleen-lezen")],
)
def test_readonly_translation(language: str, readonly: str) -> None:
    """The read-only state of a subaccount uses the established terms."""
    data = _load(COMPONENT / "translations" / f"{language}.json")
    assert data["entity"]["binary_sensor"]["readonly"]["name"] == readonly


def test_readonly_icons() -> None:
    """A read-only subaccount shows a closed lock, a writable one an open lock."""
    icons = _load(COMPONENT / "icons.json")["entity"]
    assert icons["binary_sensor"]["readonly"]["state"] == {
        "on": "mdi:lock",
        "off": "mdi:lock-open-variant",
    }
    assert icons["sensor"]["home_directory"] == {"default": "mdi:folder-home"}


def test_config_flow_steps_and_aborts_translated() -> None:
    """Every form step and every abort reason of the flow has texts."""
    tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
    steps = {
        node.name.removeprefix("async_step_")
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name.startswith("async_step_")
    }
    # reauth only forwards to reauth_confirm and shows no form
    steps.discard("reauth")
    assert steps == {"user", "storage_box", "reauth_confirm", "reconfigure"}
    for step in steps:
        texts = EN["config"]["step"][step]
        assert texts["title"], step
        assert texts["description"], step
    # async_update_reload_and_abort picks the reason by the flow's source;
    # _abort_if_unique_id_configured guards against a race
    assert set(EN["config"]["abort"]) == {
        "already_configured",
        "reauth_successful",
        "reconfigure_successful",
    }
