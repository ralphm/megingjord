from typing import AsyncIterator

from attrs import define, field
from pulsectl import PulseDisconnected, PulseEventInfo, PulseSinkInfo
from pulsectl_asyncio import PulseAsync


@define
class PulseAudioCoordinator:
    pulse: PulseAsync = field(init=False, default=None)

    async def connect(self):
        self.pulse = PulseAsync("megingjord")
        await self.pulse.connect()
        print("Connected to PulseAudio")

    async def listen(self, mask) -> AsyncIterator[PulseEventInfo]:
        print(f"Waiting for {mask!r} events")

        while True:
            if self.pulse is None:
                await self.connect()

            print(f"Subscribing to {mask!r} events")

            try:
                async for event in self.pulse.subscribe_events(mask):
                    print(f"Yielding {event!r}")
                    yield event
            except PulseDisconnected:
                print("Disconnected from PulseAudio")
                self.pulse = None
                raise

    async def get_default_sink(self) -> PulseSinkInfo:
        info = await self.pulse.server_info()
        return info.default_sink_name

    async def listen_default_sink(self) -> AsyncIterator[PulseSinkInfo]:
        while True:
            if self.pulse is None:
                await self.connect()

            yield await self.get_default_sink()

            try:
                async for event in self.listen("server"):
                    yield await self.get_default_sink()
            except PulseDisconnected:
                print("hoi")

    async def set_default_sink(self, name: str):
        new_sink = await self.pulse.get_sink_by_name(name)
        await self.pulse.default_set(new_sink)
