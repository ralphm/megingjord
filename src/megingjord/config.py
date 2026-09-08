# SPDX-License-Identifier: MIT

"""
YAML configuration loading.

The configuration drives the whole deck setup: theme, dials, keys, and
the coordinators (PulseAudio, Home Assistant, Google Meet). Secrets are
injected from environment variables with ``${VAR}`` placeholders.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web
from attrs import asdict, define, field

# Imported for their config type registration side effects.
from . import ha as _ha_module  # noqa: F401
from . import pulseaudio as _pulseaudio_module  # noqa: F401
from . import streamdeck as _streamdeck_module  # noqa: F401
from .google_meet import GoogleMeetCoordinator
from .ha import HAWebSocketClient
from .pulseaudio import PulseAudioCoordinator
from .registry import DIAL_TYPES, KEY_TYPES, BuildContext, ConfigError

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
class HomeAssistantConfig:
    """
    Home Assistant WebSocket client configuration.
    """

    url: str
    token: str


@define
class WeightConfig:
    """
    A PulseAudio output or input weight rule.
    """

    card: dict[str, Any] | None = None
    port: dict[str, Any] | None = None
    weight: int = 0


@define
class PulseAudioConfig:
    """
    PulseAudio coordinator configuration.
    """

    output_weights: list[WeightConfig] = field(factory=list)
    input_weights: list[WeightConfig] = field(factory=list)


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
class GoogleMeetConfig:
    """
    Google Meet coordinator configuration.
    """

    phases: dict[str, dict[int, str]]


@define
class Config:
    """
    The full deck configuration.
    """

    theme: str = "default"
    home_assistant: HomeAssistantConfig | None = None
    pulseaudio: PulseAudioConfig | None = None
    dials: dict[int, DialConfig] = field(factory=dict)
    keys: dict[int, KeyConfig] = field(factory=dict)
    google_meet: GoogleMeetConfig | None = None


def _build_config(data: dict[str, Any]) -> Config:
    """
    Build the config model from the parsed YAML.
    """
    home_assistant = None
    if "home_assistant" in data:
        home_assistant = HomeAssistantConfig(**data["home_assistant"])

    pulseaudio = None
    if "pulseaudio" in data:
        pulseaudio = PulseAudioConfig(
            output_weights=[
                WeightConfig(**weight)
                for weight in data["pulseaudio"].get("output_weights", [])
            ],
            input_weights=[
                WeightConfig(**weight)
                for weight in data["pulseaudio"].get("input_weights", [])
            ],
        )

    google_meet = None
    if "google_meet" in data:
        google_meet = GoogleMeetConfig(phases=data["google_meet"]["phases"])

    config = Config(
        theme=data.get("theme", "default"),
        home_assistant=home_assistant,
        pulseaudio=pulseaudio,
        dials={
            key: DialConfig(**value)
            for key, value in data.get("dials", {}).items()
        },
        keys={
            key: KeyConfig(**value)
            for key, value in data.get("keys", {}).items()
        },
        google_meet=google_meet,
    )

    _validate(config)
    return config


def _validate(config: Config) -> None:
    """
    Validate the configuration.

    The Google Meet phases are alternative layouts (only one is active
    at a time), so they may share key numbers with each other; they
    must not collide with the statically registered keys.
    """
    claimed: dict[int, str] = {}
    for key in config.keys:
        claimed[key] = f"keys[{key}]"

    if config.google_meet:
        for phase, phase_keys in config.google_meet.phases.items():
            for key in phase_keys:
                if key in claimed:
                    raise ConfigError(
                        f"Key {key} claimed by both {claimed[key]} and "
                        f"google_meet.phases.{phase}"
                    )

    for dial, dial_config in config.dials.items():
        if dial_config.type not in DIAL_TYPES:
            raise ConfigError(
                f"dials[{dial}]: unknown type {dial_config.type!r}"
            )

    for key, key_config in config.keys.items():
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
    app["color_theme"] = config.theme

    controller = app["deck_controller"]

    pulse = None
    if config.pulseaudio:
        pulse = PulseAudioCoordinator(
            app,
            output_weights=[
                asdict(weight) for weight in config.pulseaudio.output_weights
            ],
            input_weights=[
                asdict(weight) for weight in config.pulseaudio.input_weights
            ],
        )

    ha = None
    if config.home_assistant:
        ha = HAWebSocketClient(
            app,
            url=config.home_assistant.url,
            token=config.home_assistant.token,
        )

    context = BuildContext(app=app, pulse=pulse, ha=ha)

    for dial, dial_config in config.dials.items():
        builder = DIAL_TYPES.get(dial_config.type)
        if builder is None:
            raise ConfigError(
                f"dials[{dial}]: unknown type {dial_config.type!r}"
            )
        controller.register_dial(builder(dial, dial_config, context))

    for key, key_config in config.keys.items():
        builder = KEY_TYPES.get(key_config.type)
        if builder is None:
            raise ConfigError(f"keys[{key}]: unknown type {key_config.type!r}")
        controller.register_key(builder(key, key_config, context))

    if config.google_meet:
        GoogleMeetCoordinator(app, controller, config.google_meet.phases)
