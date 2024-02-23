"""
Streamdeck utilities.
"""

import asyncio
from contextlib import suppress
from typing import Protocol

from attrs import define, field

from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import StreamDeck

class Key(Protocol):
    deck: StreamDeck
    key: int

    def init(self, key: int) -> None:
        ...
    async def start(self) -> None:
        ...
    async def stop(self) -> None:
        ...
    async def on_key_change(self, key_state: bool) -> None:
        ...

class LCDKey(Key):
    ...


class Dial(Protocol):
    ...


class Panel(Protocol):
    ...


@define
class DeckController:
    """
    Stream Deck controller
    """

    deck: StreamDeck = field(init=False, default=None)
    done: asyncio.Event = field(init=False, factory=asyncio.Event)
    keys: dict[int, Key] = field(init=False, factory=dict)

    def register_key(self, key: Key) -> None:
        self.keys[key.key] = key

    def unregister_key(self, key: Key) -> None:
        del self.keys[key.key]

    async def on_key_change(self, deck, key, key_state):
        if deck != self.deck or key not in self.keys:
            return

        await self.keys[key].on_key_change(key_state)

    async def listen_stream_deck(self, deck):
        deck.set_key_callback_async(self.on_key_change)

    async def listen(self):
        streamdecks = DeviceManager().enumerate()

        for _, deck in enumerate(streamdecks):
            self.deck = deck

            deck.open()
            deck.reset()

            await self.listen_stream_deck(deck)

            deck.set_brightness(100)

            for key in self.keys.values():
                await key.start(deck)

            break

        with suppress(asyncio.CancelledError):
            await self.done.wait()

    async def start(self):
        listen_task = asyncio.create_task(self.listen())

        with suppress(asyncio.CancelledError):
            await listen_task

        await self.stop()

    async def stop(self):
        # Clean up

        for key in self.keys.values():
            await key.stop()

        self.deck.close()
        await asyncio.sleep(2)
