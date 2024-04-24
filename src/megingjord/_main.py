"""
Main entry.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress
from typing import AsyncIterator, Callable

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
    app: web.Application = field(factory=web.Application)

    async def run_background_tasks(
        self, app: web.Application
    ) -> AsyncIterator[None]:
        """
        Run background tasks.
        """

        task = app.loop.create_task(app["deck_controller"].start())

        yield

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def start(self) -> None:
        """
        Start Megingjord.
        """
        self.app["deck_controller"] = DeckController(self.app)

        self.app.cleanup_ctx.append(self.run_background_tasks)

        self.setup(self.app)

        web.run_app(self.app, shutdown_timeout=0, port=2394)


def main(
    setup: Callable[[web.Application], None],
    level: int = logging.INFO,
) -> None:
    """
    Main entry.
    """
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        level=level,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        megingjord = Megingjord(setup=setup)
        logger.debug(megingjord)
        megingjord.start()
        logger.info("Exiting normally.")
        sys.exit(0)
    except Exception as exc:  # pylint: disable=W0718
        logger.critical(f"Exiting abnormally: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Bye!")
