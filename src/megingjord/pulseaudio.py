"""
PulseAudio utilities.
"""

import asyncio
from contextlib import suppress
from typing import AsyncIterator

from attrs import define, field
from pulsectl import PulseDisconnected, PulseEventInfo, PulseSinkInfo
from pulsectl_asyncio import PulseAsync


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
