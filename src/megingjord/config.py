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
    BuildContext,
    ConfigError,
    is_device_section,
    section_namespace,
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
    interprets its own section via the registry. The logging level is
    app-level configuration, not an integration section.
    """

    sections: dict[str, dict[str, Any]] = field(factory=dict)
    logging_level: str = "info"


LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


def _build_config(data: dict[str, Any]) -> Config:
    """
    Build the config model from the parsed YAML.
    """
    logging_data = data.pop("logging", {})
    if not isinstance(logging_data, dict):
        raise ConfigError("Section 'logging' must be a mapping")
    level = logging_data.get("level", "info")
    if level not in LOG_LEVELS:
        raise ConfigError(f"logging: unknown level {level!r}")

    for name, value in data.items():
        if value is None:
            # An empty section (e.g. ``google_meet:``) is valid.
            data[name] = {}
        elif not isinstance(value, dict):
            raise ConfigError(f"Section {name!r} must be a mapping")

    config = Config(sections=data, logging_level=level)

    _validate(config)
    return config


def _validate(config: Config) -> None:
    """
    Validate the configuration.
    """
    for name in config.sections:
        if section_namespace(name) is None:
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


def setup_from_config(app: web.Application, config: Config) -> None:
    """
    Set up the deck from the configuration.

    The device integrations (e.g. the streamdeck) are started first;
    they request the integrations they need, which interpret their own
    configuration and start themselves.
    """
    context = BuildContext(
        app=app, controller=app["deck_controller"], config=config
    )

    for name in config.sections:
        if is_device_section(name):
            namespace = section_namespace(name)
            if namespace is not None:
                context.request(namespace)
