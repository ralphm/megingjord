"""
PulseAudio utilities.
"""

import asyncio
from contextlib import suppress
from typing import AsyncIterator

from attrs import define, field
from pulsectl import PulseDisconnected, PulseEventInfo, PulseSinkInfo
from pulsectl_asyncio import PulseAsync

from StreamDeck.Devices.StreamDeck import StreamDeck


@define
class PulseAudioCoordinator:
    """
    PulseAudio coordinator.

    This helper connects to PulseAudio, waiting for server events and yielding
    changes to the current default sink.
    """

    pulse: PulseAsync | None = field(init=False, default=None)
    started: bool = field(init=False, default=False)
    subscribers: dict[object, asyncio.Queue[PulseEventInfo]] = field(
        init=False, factory=dict
    )

    async def get_pulse(self) -> PulseAsync:
        """
        Get PulseAudio instance and optionally connect.
        """
        if self.pulse:
            return self.pulse

        self.pulse = PulseAsync("megingjord")
        await self.pulse.connect()
        print("Connected to PulseAudio")
        return self.pulse

    async def listen(self) -> None:
        """
        Subscribe for PulseAudio server events.
        """
        print("Waiting for events")

        while True:
            pulse = await self.get_pulse()

            assert self.pulse is not None

            await self.pulse._connected.wait()

            print("Subscribing to events")

            try:
                async for event in pulse.subscribe_events("server"):
                    print(f"Yielding {event!r}")
                    for queue in self.subscribers.values():
                        await queue.put(event)
            except PulseDisconnected:
                print("Disconnected from PulseAudio")
                self.pulse = None
            except Exception as exc:
                print(f"oops: {exc}")
                raise

    async def start(self):
        if self.started:
            return

        listen_task = asyncio.create_task(self.listen())

        self.started = True

        with suppress(asyncio.CancelledError):
            await listen_task

        self.started = False

        for queue in self.subscribers.values():
            await queue.put(None)

    async def get_default_sink(self) -> PulseSinkInfo:
        """
        Extract default sink from server info.
        """
        pulse = await self.get_pulse()
        info = await pulse.server_info()
        return info.default_sink_name

    async def listen_default_sink(self) -> AsyncIterator[PulseSinkInfo]:
        """
        Listen for default sink changes.
        """
        key = object()
        queue: asyncio.Queue[PulseEventInfo] = asyncio.Queue()
        self.subscribers[key] = queue

        asyncio.create_task(self.start())

        done = False

        while not done:
            yield await self.get_default_sink()
            done = await queue.get() is None

    async def set_default_sink(self, name: str) -> None:
        """
        Set default sink.
        """
        pulse = await self.get_pulse()
        new_sink = await pulse.get_sink_by_name(name)
        await pulse.default_set(new_sink)


@define
class PulseDefaultSinkKey:
    """
    Stream Deck key for switching the default PulseAudio sink.
    """

    key: int
    outputs: dict = field()

    deck: StreamDeck = field(init=False)
    output: str = field(init=False)
    pulse: PulseAudioCoordinator = field(init=False)

    async def start(self, deck: StreamDeck):
        """
        Start this key.
        """
        self.deck = deck
        self.pulse = PulseAudioCoordinator()
        async for sink_name in self.pulse.listen_default_sink():
            await self.on_sink(sink_name)

    async def stop(self):
        """
        Stop this key.
        """
        self.deck.set_key_image(self.key, None)

    async def on_key_change(self, key_state):
        """
        The Stream Deck key was pressed or released.
        """
        # Ignore key release
        if not key_state:
            return

        for name, _ in self.outputs.items():
            print(name)
            if name != self.output:
                new_name = name
                break

        try:
            print(f"new name: {new_name}")
            await self.pulse.set_default_sink(self.outputs[new_name]["sink"])
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
