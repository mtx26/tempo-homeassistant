"""Hassfest/HACS-like static checks that can run locally, plus config entry migration."""

from __future__ import annotations

import ast
import json
import re
import struct
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tempo.config_flow import TempoConfigFlow
from custom_components.tempo.const import DOMAIN
from custom_components.tempo.sensor import SENSORS

from .conftest import BASE, TOKEN, mock_backend, setup_entry

REPO = Path(__file__).parents[1]
COMPONENT = REPO / "custom_components" / DOMAIN
TESTED_HA = ("2026.2.3", "2026.10.4")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _keys(data: Any, prefix: str = "") -> set[str]:
    if not isinstance(data, dict):
        return {prefix}
    out: set[str] = set()
    for key, value in data.items():
        out |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return out


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"{(\w+)}", text))


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def test_manifest() -> None:
    """Required keys, hassfest key order (domain, name, then sorted) and semver version."""
    manifest = _json(COMPONENT / "manifest.json")
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    for key in ("documentation", "issue_tracker", "codeowners", "version", "iot_class"):
        assert manifest[key], key
    assert manifest["domain"] == DOMAIN
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["version"] == "0.3.0"
    assert manifest["config_flow"] is True
    assert "conversation" in manifest["dependencies"]
    assert manifest["documentation"].startswith("https://")
    assert manifest["issue_tracker"].startswith("https://")


def test_hacs_json() -> None:
    """hacs.json: minimum Home Assistant not above the oldest version we tested."""
    hacs = _json(REPO / "hacs.json")
    assert hacs["name"] == "Tempo"
    assert _version(hacs["homeassistant"]) <= _version(TESTED_HA[0])
    assert set(hacs) <= {"name", "homeassistant", "render_readme", "hide_default_branch"}


def test_brand_images() -> None:
    """brand/ holds PNG icons of the expected sizes (custom integration brand folder)."""
    for name, size in (
        ("icon.png", 256),
        ("icon@2x.png", 512),
        ("dark_icon.png", 256),
        ("dark_icon@2x.png", 512),
    ):
        data = (COMPONENT / "brand" / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (size, size), name


def test_translations_have_identical_keys() -> None:
    """strings.json, en.json and fr.json define exactly the same keys and placeholders."""
    strings = _json(COMPONENT / "strings.json")
    en = _json(COMPONENT / "translations" / "en.json")
    fr = _json(COMPONENT / "translations" / "fr.json")
    assert strings == en
    assert _keys(strings) == _keys(fr)

    def leaves(data: Any, prefix: str = "") -> dict[str, str]:
        if isinstance(data, str):
            return {prefix: data}
        out: dict[str, str] = {}
        for key, value in data.items():
            out |= leaves(value, f"{prefix}.{key}")
        return out

    fr_leaves = leaves(fr)
    for key, text in leaves(strings).items():
        assert _placeholders(text) == _placeholders(fr_leaves[key]), key


def test_translations_cover_code() -> None:
    """Every translation_key used in code, entity and service exists in strings.json."""
    strings = _json(COMPONENT / "strings.json")
    used: set[str] = set()
    for source in COMPONENT.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.keyword)
                and node.arg == "translation_key"
                and isinstance(node.value, ast.Constant)
            ):
                used.add(str(node.value.value))
    entity_keys = {d.translation_key for d in SENSORS} | {"tasks", "assistant"}
    exception_keys = used - entity_keys
    assert exception_keys <= set(strings["exceptions"]), exception_keys - set(strings["exceptions"])
    for description in SENSORS:
        assert description.translation_key in strings["entity"]["sensor"]
    assert "button" not in strings["entity"]  # no business buttons: conversation only
    assert "services" not in strings  # no business service actions
    assert not (COMPONENT / "services.yaml").exists()
    assert "tasks" in strings["entity"]["todo"]
    assert "assistant" in strings["entity"]["conversation"]
    # Enum sensor states are all translated.
    for description in SENSORS:
        if description.options:
            states = strings["entity"]["sensor"][description.translation_key]["state"]
            assert set(description.options) == set(states), description.key


async def test_migrate_entry_from_1_1(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 1.1 entry is migrated to the current minor version and loads."""
    mock_backend(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BASE,
        version=1,
        minor_version=1,
        data={"url": BASE, "api_token": TOKEN, "verify_ssl": True},
    )
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (
        TempoConfigFlow.VERSION,
        TempoConfigFlow.MINOR_VERSION,
    )
    assert entry.data["api_token"] == TOKEN


async def test_migrate_entry_from_future_major_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An entry from a newer major version is not downgraded."""
    mock_backend(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BASE,
        version=2,
        minor_version=1,
        data={"url": BASE, "api_token": TOKEN, "verify_ssl": True},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
