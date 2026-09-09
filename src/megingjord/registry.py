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

from attrs import define, field


class ConfigError(Exception):
    """
    Invalid configuration.
    """


def build_config(model: type, path: str, data: dict[str, Any]) -> Any:
    """
    Build a config model from a mapping, converting construction
    errors to ConfigError with the config path.
    """
    try:
        return model(**data)
    except TypeError as exc:
        message = str(exc)
        if ".__init__() " in message:
            message = message.partition(".__init__() ")[2]
        raise ConfigError(f"{path}: {message}") from exc


@define
class BuildContext:
    """
    Shared objects available to type builders and section handlers.

    Integrations are loaded and started on demand via request(); the
    context grows as integrations are requested.
    """

    app: Any
    controller: Any = None
    config: Any = None
    pulseaudio: Any = None
    ha: Any = None
    google_meet: Any = None
    _started: set[str] = field(factory=set, init=False)

    def request(self, namespace: str) -> None:
        """
        Load and start an integration on demand.

        The integration module is imported (running its registration
        side effects) and its configuration sections are interpreted,
        so the integration starts what it needs.
        """
        if namespace in self._started:
            return
        load_integration(namespace)
        self._started.add(namespace)
        integration = INTEGRATIONS[namespace]
        for section in integration.sections:
            data = self.config.sections.get(section)
            if data is not None:
                handler = SECTION_HANDLERS.get(section)
                if handler is None:
                    raise ConfigError(
                        f"Unknown configuration section {section!r}"
                    )
                handler(data, self)


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
# Device integrations (e.g. the streamdeck) are interpreted after the
# support integrations, since they consume the built context.
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


def section_namespace(name: str) -> str | None:
    """
    The integration namespace owning a configuration section.
    """
    for namespace, integration in INTEGRATIONS.items():
        if name in integration.sections:
            return namespace
    return None


def is_device_section(name: str) -> bool:
    """
    Whether a configuration section belongs to a device integration.
    """
    namespace = section_namespace(name)
    if namespace is None:
        return False
    return INTEGRATIONS[namespace].device


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
