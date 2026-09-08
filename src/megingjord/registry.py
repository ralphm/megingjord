# SPDX-License-Identifier: MIT

"""
Registry of config-driven dial and key types.

Each integration registers its dial and key builders here at import
time; the config loader looks types up in the registry instead of
dispatching on literal type names.
"""

from __future__ import annotations

from typing import Any, Callable

from attrs import define


class ConfigError(Exception):
    """
    Invalid configuration.
    """


@define
class BuildContext:
    """
    Shared objects available to type builders.
    """

    app: Any
    pulse: Any = None
    ha: Any = None


DialBuilder = Callable[[int, Any, BuildContext], Any]
KeyBuilder = Callable[[int, Any, BuildContext], Any]

DIAL_TYPES: dict[str, DialBuilder] = {}
KEY_TYPES: dict[str, KeyBuilder] = {}


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
