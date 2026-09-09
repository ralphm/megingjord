# SPDX-License-Identifier: MIT

"""
Registry of config-driven dial and key types.

Each integration registers its dial and key builders and its
configuration section handlers here at import time. The host declares
the namespace-to-module map; integrations are imported on demand when
their namespace is referenced by a type or their configuration section
is present.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

from attrs import define


class ConfigError(Exception):
    """
    Invalid configuration.
    """


@define
class BuildContext:
    """
    Shared objects available to type builders and section handlers.
    """

    app: Any
    controller: Any = None
    config: Any = None
    pulse: Any = None
    ha: Any = None
    meet: Any = None


DialBuilder = Callable[[int, Any, BuildContext], Any]
KeyBuilder = Callable[[int, Any, BuildContext], Any]
SectionHandler = Callable[[dict[str, Any], BuildContext], None]

DIAL_TYPES: dict[str, DialBuilder] = {}
KEY_TYPES: dict[str, KeyBuilder] = {}
SECTION_HANDLERS: dict[str, SectionHandler] = {}


@define
class Integration:
    """
    An integration: the configuration sections it owns and whether it
    is a device.
    """

    sections: tuple[str, ...] = ()
    device: bool = False


# Namespace -> integration. The module name equals the namespace.
# Device integrations (streamdeck now, busy bar later) are interpreted
# after the support integrations, since they consume the built context.
INTEGRATIONS: dict[str, Integration] = {
    "streamdeck": Integration(sections=("streamdeck",), device=True),
    "ha": Integration(sections=("home_assistant",)),
    "pulseaudio": Integration(sections=("pulseaudio",)),
    "google_meet": Integration(sections=("google_meet",)),
}

_loaded_integrations: set[str] = set()


def register_dial_type(name: str, builder: DialBuilder) -> None:
    """
    Register a dial type builder.
    """
    DIAL_TYPES[name] = builder


def register_key_type(name: str, builder: KeyBuilder) -> None:
    """
    Register a key type builder.
    """
    KEY_TYPES[name] = builder


def register_section(name: str, handler: SectionHandler) -> None:
    """
    Register a configuration section handler.
    """
    SECTION_HANDLERS[name] = handler


def is_device_section(name: str) -> bool:
    """
    Whether a configuration section belongs to a device integration.
    """
    for integration in INTEGRATIONS.values():
        if name in integration.sections:
            return integration.device
    return False


def load_integration(namespace: str) -> None:
    """
    Import the integration module for a namespace.

    Importing the module runs its registration side effects.
    """
    if namespace in _loaded_integrations:
        return
    if namespace not in INTEGRATIONS:
        raise ConfigError(f"Unknown integration {namespace!r}")
    importlib.import_module(f".{namespace}", __package__)
    _loaded_integrations.add(namespace)
