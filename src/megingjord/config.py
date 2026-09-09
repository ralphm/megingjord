# SPDX-License-Identifier: MIT

"""
YAML configuration loading.

The configuration is a set of sections, one per integration. The
streamdeck section is the deck device; other integrations (ha,
pulseaudio, google_meet) are imported on demand when their namespace
is referenced by a dial or key type or their section is present. Each
integration interprets its own section via the registry. Secrets are
injected from environment variables with ``${VAR}`` placeholders.
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
    INTEGRATIONS,
    SECTION_HANDLERS,
    BuildContext,
    ConfigError,
    is_device_section,
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
class Config:
    """
    The full deck configuration.

    The sections are kept as raw mappings; each integration
    interprets its own section via the registry.
    """

    sections: dict[str, dict[str, Any]] = field(factory=dict)


def _referenced_namespaces(config: Config) -> set[str]:
    """
    The integration namespaces referenced by types and sections.

    The dial and key types live in the streamdeck section; a type with
    a namespace prefix (e.g. ``ha.entity``) references its integration.
    """
    namespaces: set[str] = set()

    streamdeck_data = config.sections.get("streamdeck", {})
    for dial_config in streamdeck_data.get("dials", {}).values():
        if "." in dial_config["type"]:
            namespaces.add(dial_config["type"].partition(".")[0])

    for key_config in streamdeck_data.get("keys", {}).values():
        if "." in key_config["type"]:
            namespaces.add(key_config["type"].partition(".")[0])

    for name in config.sections:
        for namespace, integration in INTEGRATIONS.items():
            if name in integration.sections:
                namespaces.add(namespace)

    return namespaces


def _load_integrations(config: Config) -> None:
    """
    Import the integrations referenced by the configuration.
    """
    for namespace in _referenced_namespaces(config):
        load_integration(namespace)


def _build_config(data: dict[str, Any]) -> Config:
    """
    Build the config model from the parsed YAML.
    """
    for name, value in data.items():
        if not isinstance(value, dict):
            raise ConfigError(f"Section {name!r} must be a mapping")

    config = Config(sections=data)

    _load_integrations(config)
    _validate(config)
    return config


def _validate(config: Config) -> None:
    """
    Validate the configuration.
    """
    for name in config.sections:
        if name not in SECTION_HANDLERS:
            raise ConfigError(f"Unknown configuration section {name!r}")


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


def _interpret_sections(
    config: Config, context: BuildContext, device: bool
) -> None:
    """
    Interpret the configuration sections of one kind.
    """
    for name, data in config.sections.items():
        if is_device_section(name) != device:
            continue
        handler = SECTION_HANDLERS.get(name)
        if handler is None:
            raise ConfigError(f"Unknown configuration section {name!r}")
        handler(data, context)


def setup_from_config(app: web.Application, config: Config) -> None:
    """
    Set up the deck from the configuration.

    Device sections (the streamdeck) are interpreted after the support
    sections, since they consume the built context.
    """
    context = BuildContext(
        app=app, controller=app["deck_controller"], config=config
    )

    _interpret_sections(config, context, device=False)
    _interpret_sections(config, context, device=True)
