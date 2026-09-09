# SPDX-License-Identifier: MIT

"""
YAML configuration loading.

The configuration drives the whole deck setup. The ``streamdeck``
section is the host: theme, dials and keys. The other sections belong
to integrations, which are imported on demand when their namespace is
referenced by a dial or key type or their section is present. Each
integration interprets its own section and registers its types in the
registry. Secrets are injected from environment variables with
``${VAR}`` placeholders.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web
from attrs import define, field

from .registry import (
    DIAL_TYPES,
    INTEGRATIONS,
    KEY_TYPES,
    SECTION_HANDLERS,
    BuildContext,
    ConfigError,
    load_integration,
)

__all__ = [
    "Config",
    "ConfigError",
    "load_config",
    "setup_from_config",
]

ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class UniqueKeyLoader(yaml.SafeLoader):
    """
    A YAML loader that rejects duplicate mapping keys.
    """


def _construct_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """
    Construct a mapping, rejecting duplicate keys.
    """
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConfigError(
                f"Duplicate key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def interpolate_env(value: Any) -> Any:
    """
    Replace ``${VAR}`` placeholders with environment variables.
    """
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(f"Environment variable {name} is not set")
            return os.environ[name]

        return ENV_VAR_RE.sub(replace, value)

    if isinstance(value, list):
        return [interpolate_env(item) for item in value]

    if isinstance(value, dict):
        return {key: interpolate_env(item) for key, item in value.items()}

    return value


@define
class DialConfig:
    """
    A dial registration.
    """

    type: str
    entity_id: str | None = None
    arm_service: str | None = None


@define
class KeyConfig:
    """
    A key registration.
    """

    type: str
    entity_id: str | None = None
    arm_service: str | None = None
    icon: str | None = None


@define
class StreamDeckConfig:
    """
    The host configuration: theme, dials and keys.
    """

    theme: str = "default"
    dials: dict[int, DialConfig] = field(factory=dict)
    keys: dict[int, KeyConfig] = field(factory=dict)


@define
class Config:
    """
    The full deck configuration.

    The integration sections are kept as raw mappings; each
    integration interprets its own section via the registry.
    """

    streamdeck: StreamDeckConfig = field(factory=StreamDeckConfig)
    sections: dict[str, dict[str, Any]] = field(factory=dict)


def _referenced_namespaces(config: Config) -> set[str]:
    """
    The integration namespaces referenced by types and sections.
    """
    namespaces: set[str] = set()

    for dial_config in config.streamdeck.dials.values():
        if "." in dial_config.type:
            namespaces.add(dial_config.type.partition(".")[0])

    for key_config in config.streamdeck.keys.values():
        if "." in key_config.type:
            namespaces.add(key_config.type.partition(".")[0])

    for name in config.sections:
        for namespace, sections in INTEGRATIONS.items():
            if name in sections:
                namespaces.add(namespace)

    return namespaces


def _load_integrations(config: Config) -> None:
    """
    Import the host and the integrations referenced by the config.
    """
    load_integration("streamdeck")
    for namespace in _referenced_namespaces(config):
        load_integration(namespace)


def _build_config(data: dict[str, Any]) -> Config:
    """
    Build the config model from the parsed YAML.
    """
    streamdeck_data = data.get("streamdeck", {})
    if not isinstance(streamdeck_data, dict):
        raise ConfigError("The streamdeck section must be a mapping")

    config = Config(
        streamdeck=StreamDeckConfig(
            theme=streamdeck_data.get("theme", "default"),
            dials={
                key: DialConfig(**value)
                for key, value in streamdeck_data.get("dials", {}).items()
            },
            keys={
                key: KeyConfig(**value)
                for key, value in streamdeck_data.get("keys", {}).items()
            },
        ),
        sections={
            name: value for name, value in data.items() if name != "streamdeck"
        },
    )

    _load_integrations(config)
    _validate(config)
    return config


def _validate(config: Config) -> None:
    """
    Validate the configuration.

    The Google Meet phases are alternative layouts (only one is active
    at a time), so they may share key numbers with each other; they
    must not collide with the statically registered keys.
    """
    for name in config.sections:
        if name not in SECTION_HANDLERS:
            raise ConfigError(f"Unknown configuration section {name!r}")

    phases = config.sections.get("google_meet", {}).get("phases", {})
    claimed: dict[int, str] = {}
    for key in config.streamdeck.keys:
        claimed[key] = f"keys[{key}]"

    for phase, phase_keys in phases.items():
        for key in phase_keys:
            if key in claimed:
                raise ConfigError(
                    f"Key {key} claimed by both {claimed[key]} and "
                    f"google_meet.phases.{phase}"
                )

    for dial, dial_config in config.streamdeck.dials.items():
        if dial_config.type not in DIAL_TYPES:
            raise ConfigError(
                f"dials[{dial}]: unknown type {dial_config.type!r}"
            )

    for key, key_config in config.streamdeck.keys.items():
        if key_config.type not in KEY_TYPES:
            raise ConfigError(f"keys[{key}]: unknown type {key_config.type!r}")


def load_config(path: Path) -> Config:
    """
    Load and validate the configuration from a YAML file.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=UniqueKeyLoader)
    except OSError as exc:
        raise ConfigError(
            f"Cannot read configuration file {path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if data is None:
        data = {}

    if not isinstance(data, dict):
        raise ConfigError(f"Configuration in {path} must be a mapping")

    return _build_config(interpolate_env(data))


def setup_from_config(app: web.Application, config: Config) -> None:
    """
    Set up the deck from the configuration.
    """
    app["color_theme"] = config.streamdeck.theme

    controller = app["deck_controller"]
    context = BuildContext(app=app, controller=controller)

    for name, data in config.sections.items():
        handler = SECTION_HANDLERS.get(name)
        if handler is None:
            raise ConfigError(f"Unknown configuration section {name!r}")
        handler(data, context)

    for dial, dial_config in config.streamdeck.dials.items():
        builder = DIAL_TYPES.get(dial_config.type)
        if builder is None:
            raise ConfigError(
                f"dials[{dial}]: unknown type {dial_config.type!r}"
            )
        controller.register_dial(builder(dial, dial_config, context))

    for key, key_config in config.streamdeck.keys.items():
        builder = KEY_TYPES.get(key_config.type)
        if builder is None:
            raise ConfigError(f"keys[{key}]: unknown type {key_config.type!r}")
        controller.register_key(builder(key, key_config, context))
