"""
Main entry.
"""

import asyncio
import logging
import signal
from pathlib import Path
from typing import Callable

from .streamdeck import DeckController


async def main(icon_path: Path, setup: Callable, level=logging.INFO):
    """
    Main entry.
    """
    logging.basicConfig(level=level)

    loop = asyncio.get_event_loop()
    main_task = asyncio.current_task(loop)

    async def shutdown() -> None:
        """
        Cancel all running async tasks (other than this one) when called.
        By catching asyncio.CancelledError, any running task can perform
        any necessary cleanup when it's cancelled.
        """
        tasks = []
        for task in asyncio.all_tasks(loop):
            if task not in (asyncio.current_task(loop), main_task):
                task.cancel()
                tasks.append(task)
        await asyncio.gather(*tasks, return_exceptions=True)

    # register signal handlers to stop tasks
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    controller = DeckController(icon_path=icon_path)
    await setup(controller)

    await controller.start()

    print()
