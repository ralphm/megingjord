#!/usr/bin/env python3
"""
Test for megingjord.
"""

import asyncio
import io
import signal
from contextlib import suppress
from typing import Dict

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

    outputs: Dict

    deck: StreamDeck = field(init=False)
    key: int = field(init=False)
    output: str = field(init=False)
    pulse: PulseAudioCoordinator = field(init=False)

    async def activate(self, deck, key):
        self.deck = deck
        self.key = key

        self.pulse = PulseAudioCoordinator()
        async for sink_name in self.pulse.listen_default_sink():
            await self.on_sink(sink_name)

    async def on_press(self):
        for name, output in outputs.items():
            print(name)
            if name != self.output:
                new_name = name
                break

        try:
            print(f"new name: {new_name}")
            await self.pulse.set_default_sink(outputs[new_name]["sink"])
            self.output = new_name
        except Exception as e:
            print(e)

    async def on_sink(self, sink_name):
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
        if deck != self.deck or key != 0 or not key_state:
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

        await self.done.wait()


async def main():
    loop = asyncio.get_event_loop()

    key = PulseDefaultSinkKey(outputs)
    controller = Controller(key)

    # Run listen() coroutine in task to allow cancelling it
    listen_task = asyncio.create_task(controller.listen())

    # cancel listener when program is asked to terminate
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        loop.add_signal_handler(sig, listen_task.cancel)

    with suppress(asyncio.CancelledError):
        await listen_task


# Run event loop until main_task finishes
asyncio.run(main())
