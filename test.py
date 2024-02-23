#!/usr/bin/env python3
"""
Test for megingjord.
"""

import asyncio
import io
import signal

from PIL import Image

from megingjord.pulseaudio import PulseAudioCoordinator, PulseDefaultSinkKey
from megingjord.streamdeck import DeckController

speaker = Image.open("speaker.png")
speaker_byte_arr = io.BytesIO()
speaker.save(speaker_byte_arr, format="JPEG")
speaker_bytes = speaker_byte_arr.getvalue()

headphones = Image.open("headphones.png")
headphones_byte_arr = io.BytesIO()
headphones.save(headphones_byte_arr, format="JPEG")
headphones_bytes = headphones_byte_arr.getvalue()

outputs = {
    "HDMI": {
        "sink": "alsa_output.pci-0000_00_1f.3.hdmi-stereo",
        "icon": speaker_bytes,
    },
    "shure_mv7": {
        "sink": "alsa_output.usb-Shure_Inc_Shure_MV7-00.iec958-stereo",
        "icon": headphones_bytes,
    },
    "headphones": {
        "sink": "alsa_output.pci-0000_00_1f.3.analog-stereo",
        "icon": headphones_bytes,
    },
}


async def main():
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
                print(f"Shutting down {task}")
                task.cancel()
                tasks.append(task)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        print(f"Shutdown results: {results}")

    # register signal handlers to stop tasks
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    controller = DeckController()
    key = PulseDefaultSinkKey(0, outputs)
    controller.register_key(key)
    await controller.start()

    print()


# Run event loop until main_task finishes
asyncio.run(main())
