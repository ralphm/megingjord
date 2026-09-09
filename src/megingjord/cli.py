# SPDX-License-Identifier: MIT

"""
Command line entry point.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from xdg_base_dirs import xdg_config_home

from ._main import main
from .config import ConfigError, load_config, setup_from_config


def get_config_path(config: str | None) -> Path:
    """
    Resolve the configuration file path.
    """
    if config:
        return Path(config)
    return xdg_config_home() / "megingjord" / "config.yaml"


def run() -> None:
    """
    Run the application from the configuration.
    """
    parser = argparse.ArgumentParser(prog="megingjord")
    parser.add_argument(
        "--config",
        help="Path to the configuration file (default: "
        "~/.config/megingjord/config.yaml)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    path = get_config_path(args.config)
    if not path.exists():
        parser.error(f"Configuration file not found: {path}")

    try:
        config = load_config(path)
    except ConfigError as exc:
        parser.error(str(exc))

    main(
        setup=lambda app: setup_from_config(app, config),
        level=logging.DEBUG if args.verbose else logging.INFO,
    )
