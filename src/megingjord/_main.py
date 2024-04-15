"""
Main entry.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress
from pathlib import Path
from typing import AsyncGenerator, Callable

from aiohttp import web
from attrs import define, field

from .streamdeck import DeckController

logger = logging.getLogger(__name__)


@define
class Megingjord:
    """
    Megingjord application.
    """

    setup: Callable[[web.Application], None]
    icon_path: Path
    app: web.Application = field(factory=web.Application)

    async def run_background_tasks(
        self, app: web.Application
    ) -> AsyncGenerator:
        """
        Run background tasks.
        """

        task = app.loop.create_task(app["deck_controller"].start())

        yield

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def start(self):
        """
        Start Megingjord.
        """
        self.app["deck_controller"] = DeckController(
            self.app, icon_path=self.icon_path
        )

        self.app.cleanup_ctx.append(self.run_background_tasks)

        self.setup(self.app)

        web.run_app(self.app, shutdown_timeout=0)

        return self.app.get("cleanup_exception")


def main(icon_path: Path, setup: Callable, level=logging.INFO) -> None:
    """
    Main entry.
    """
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        level=level,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        megingjord = Megingjord(setup=setup, icon_path=icon_path)
        logger.debug(megingjord)
        exc = megingjord.start()
        if exc:
            raise exc
        logger.info("Exiting normally.")
        sys.exit(0)
    except Exception as exc:  # pylint: disable=W0718
        logger.critical(f"Exiting abnormally: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Bye!")
