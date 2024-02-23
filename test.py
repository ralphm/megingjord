#!/usr/bin/env python3
"""
Test for megingjord.
"""

import asyncio
import io
import signal
from contextlib import suppress

from attrs import define, field
from PIL import Image
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import StreamDeck

from megingjord.pulseaudio import PulseAudioCoordinator

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


@define
class PulseDefaultSinkKey:
    """
    Stream Deck key for switching the default PulseAudio sink.
    """

    outputs: dict = field()

    deck: StreamDeck = field(init=False)
    key: int = field(init=False)
    output: str = field(init=False)
    pulse: PulseAudioCoordinator = field(init=False)

    async def activate(self, deck, key):
        """
        Activate this key.
        """
        self.deck = deck
        self.key = key

        self.pulse = PulseAudioCoordinator()
        async for sink_name in self.pulse.listen_default_sink():
            await self.on_sink(sink_name)

    async def deactivate(self):
        """
        Deactivate this key.
        """
        self.deck.set_key_image(self.key, None)

    async def on_press(self):
        """
        The Stream Deck key was pressed.
        """
        for name, _ in outputs.items():
            print(name)
            if name != self.output:
                new_name = name
                break

        try:
            print(f"new name: {new_name}")
            await self.pulse.set_default_sink(outputs[new_name]["sink"])
            self.output = new_name
        except Exception as exc:
            print(exc)

    async def on_sink(self, sink_name):
        """
        The PulseAudio default sink changed.
        """
        sink = await self.pulse.pulse.get_sink_by_name(sink_name)
        print(repr(sink))

        new_output = None
        for name, output in self.outputs.items():
            if output["sink"] == sink_name:
                new_output = name

        self.output = new_output

        if not new_output:
            print(f"Unknown device: {sink_name}")
        else:
            self.deck.set_key_image(0, self.outputs[new_output]["icon"])


@define
class Controller:
    """
    Stream Deck controller
    """

    key: PulseDefaultSinkKey
    deck: StreamDeck = field(init=False, default=None)
    done: asyncio.Event = field(init=False, factory=asyncio.Event)

    async def key_change(self, deck, key, key_state):
        if deck != self.deck or key != self.key.key or not key_state:
            return

        await self.key.on_press()

    async def listen_stream_deck(self, deck):
        deck.set_key_callback_async(self.key_change)

    async def listen(self):
        streamdecks = DeviceManager().enumerate()

        for index, deck in enumerate(streamdecks):
            self.deck = deck

            deck.open()
            deck.reset()

            await self.listen_stream_deck(deck)

            deck.set_brightness(100)

            await self.key.activate(self.deck, 0)

            break

        with suppress(asyncio.CancelledError):
            await self.done.wait()

    async def start(self):
        listen_task = asyncio.create_task(self.listen())

        with suppress(asyncio.CancelledError):
            await listen_task

        # Clean up
        print(f"Cleaning up")
        await self.key.deactivate()
        self.deck.close()
        await asyncio.sleep(2)



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

    key = PulseDefaultSinkKey(outputs)
    controller = Controller(key)
    await controller.start()

    print()


# Run event loop until main_task finishes
asyncio.run(main())
