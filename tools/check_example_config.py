#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

"""
Validate config.example.yaml against the config loader.

Used as a pre-commit hook so schema drift in the example config is
caught at commit time.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("HA_TOKEN", "example")

from aiohttp import web  # noqa: E402

from megingjord.config import (  # noqa: E402
    ConfigError,
    load_config,
    setup_from_config,
)

path = Path("config.example.yaml")
try:
    config = load_config(path)
    app = web.Application()
    controller = MagicMock()
    controller.deck = MagicMock()
    app["deck_controller"] = controller
    setup_from_config(app, config)
except ConfigError as exc:
    print(f"{path}: {exc}", file=sys.stderr)
    sys.exit(1)

print(f"{path} is valid")
