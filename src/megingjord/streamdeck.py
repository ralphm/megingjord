# SPDX-License-Identifier: MIT

"""
Streamdeck utilities.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Awaitable, Callable, Protocol, Sequence

from aiohttp import web
from attrs import Attribute, define, field
from PIL import Image
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import DialEventType, StreamDeck
from StreamDeck.ImageHelpers.PILHelper import _to_native_format
from StreamDeck.Transport.Transport import TransportError

from .render import Renderer

# Key animation settings.
KEY_ANIMATION_PERIOD = 1.0
KEY_ANIMATION_FPS = 25

logger = logging.getLogger(__name__)


class Key(Protocol):
    """
    A Stream Deck Key.
    """

    controller: DeckController | None
    deck: StreamDeck
    key: int

    def __init__(self, key: int) -> None:
        """
        Initialize.
        """

    async def start(self, deck: StreamDeck) -> None:
        """
        Start the key.
        """

    async def stop(self) -> None:
        """
        Stop the key.
        """

    async def on_key_change(self, key_state: bool) -> None:
        """
        Called when the key got pressed or released.
        """


class Dial(Protocol):
    """
    A Stream Deck + dial with rendering of a tile on the LCD.
    """

    controller: DeckController | None
    deck: StreamDeck
    dial: int

    def __init__(self, dial: int) -> None:
        """
        Initialize.
        """

    async def start(self, deck: StreamDeck) -> None:
        """
        Start the dial.
        """

    async def stop(self) -> None:
        """
        Stop the dial.
        """

    async def on_dial_push(self, dial_state: bool) -> None:
        """
        Called when the dial got pressed or released.
        """

    async def on_dial_turn(self, value: int) -> None:
        """
        Called when the dial got turned.
        """

    async def render(self, mini: bool = False) -> Image.Image:
        """
        Render the portion of the LCD display (tile) for this dial.

        The parameter C{mini} is set to true if the part of the display for
        this dial is potentially obscured by an overlay. Overlays are expected
        to cover only the top half of the display, so a minimized version of
        the information rendered on the bottom half of the tile should alway be
        visible.
        """


@define
class ScrollerItem(Protocol):
    """
    An item for the scroller view.
    """

    wrapped: object

    @property
    def title(self) -> str:
        """
        The title for this item.
        """

    @property
    def subtitle(self) -> str | None:
        """
        The subtitle for optional additional information.
        """

    @property
    def icon(self) -> str:
        """
        The icon for this item.
        """

    @property
    def current(self) -> bool:
        """
        Indicator for this item being current.
        """

    @property
    def color(self) -> str | None:
        """
        Optional color override for the icon of the current item.
        """


@define
class ScrollerView:
    """
    View for a scroller displayed on the LCD tile.
    """

    items: Sequence[ScrollerItem] = field()
    selected: int = 0

    def turn(self, value: int) -> None:
        """
        Move the selection by the given amount, clamped to the items.
        """
        if value < 0:
            self.selected = max(0, self.selected + value)
        else:
            self.selected = min(len(self.items) - 1, self.selected + value)

    @items.validator
    def _check_items(
        self,
        _attribute: Attribute[Sequence[ScrollerItem]],
        value: Sequence[ScrollerItem],
    ) -> None:
        if len(value) == 0:
            raise ValueError("items must have at least one item")

    @property
    def selected_item(self) -> ScrollerItem:
        """
        Return item corresponding with selected item index.
        """
        return self.items[self.selected]


@define
class DeckController:
    """
    Stream Deck controller
    """

    # pylint: disable=R0902,R0904

    app: web.Application
    renderer: Renderer = field(init=False)
    deck: StreamDeck = field(init=False, default=None)
    done: asyncio.Event = field(init=False, factory=asyncio.Event)
    keys: dict[int, Key] = field(init=False, factory=dict)
    dials: dict[int, Dial] = field(init=False, factory=dict)

    # Status bar inhibited until this time
    status_inhibited: float = field(init=False, default=0)
    tasks: set[asyncio.Task[None]] = field(init=False, factory=set)

    # Key animations, keyed by key index.
    animations: dict[int, asyncio.Task[None]] = field(init=False, factory=dict)

    def __attrs_post_init__(self) -> None:
        """
        Initialize the renderer.
        """
        self.renderer = Renderer(self.app)

    def register_key(self, key: Key) -> None:
        """
        Register a key.
        """
        key.controller = self
        self.keys[key.key] = key

    def unregister_key(self, key: Key) -> None:
        """
        Unregister a key.
        """
        key.controller = None
        del self.keys[key.key]

    def start_key_animation(
        self, key: int, render: Callable[[float], Awaitable[bytes]]
    ) -> None:
        """
        Start a periodic animation for a key.

        The render callback is called with the animation phase in radians
        and should return the key image.
        """
        if key in self.animations and not self.animations[key].done():
            return
        self.animations[key] = asyncio.create_task(
            self._animate_key(key, render)
        )

    def stop_key_animation(self, key: int) -> None:
        """
        Stop the animation for a key.
        """
        task = self.animations.pop(key, None)
        if task is not None:
            task.cancel()

    async def _animate_key(
        self, key: int, render: Callable[[float], Awaitable[bytes]]
    ) -> None:
        """
        Run the animation loop for a key.
        """
        try:
            while True:
                phase = time.monotonic() * 2 * math.pi / KEY_ANIMATION_PERIOD
                image = await render(phase)
                self.deck.set_key_image(key, image)
                await asyncio.sleep(1 / KEY_ANIMATION_FPS)
        except asyncio.CancelledError:
            pass

    async def on_key_change(
        self, deck: StreamDeck, key: int, key_state: bool
    ) -> None:
        """
        Called when the key got pressed or released.
        """
        if deck != self.deck or key not in self.keys:
            return

        try:
            await self.keys[key].on_key_change(key_state)
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to process key change", exc_info=True)

    def register_dial(self, dial: Dial) -> None:
        """
        Register a dial.
        """
        dial.controller = self
        self.dials[dial.dial] = dial

    def unregister_dial(self, dial: Dial) -> None:
        """
        Unregister a dial.
        """
        dial.controller = None
        del self.dials[dial.dial]

    async def on_dial_change(
        self,
        deck: StreamDeck,
        dial: int,
        event_type: DialEventType,
        value: int | bool,
    ) -> None:
        """
        Called when the dial was pushed, released or turned.
        """
        if deck != self.deck or dial not in self.dials:
            return

        try:
            if event_type == DialEventType.PUSH:
                assert isinstance(value, bool)
                await self.dials[dial].on_dial_push(value)
            else:
                assert isinstance(value, int)
                await self.dials[dial].on_dial_turn(value)

        except Exception:  # pylint: disable=W0718
            logger.error("Failed to process dial change", exc_info=True)

    async def listen(self) -> None:
        """
        Find a Stream Deck, open and initialize.
        """
        streamdecks = DeviceManager().enumerate()

        if not streamdecks:
            return

        self.deck = deck = streamdecks[0]

        deck.open()
        logger.info("Opened Stream Deck")

        deck.reset()
        deck.set_key_image(0, None)  # Resets the LCD display :/

        deck.set_key_callback_async(self.on_key_change)
        deck.set_dial_callback_async(self.on_dial_change)

        deck.brightness = 100
        self.set_brightness(deck.brightness)

        for key in self.keys.values():
            task = asyncio.create_task(key.start(deck))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        for dial in self.dials.values():
            task = asyncio.create_task(dial.start(deck))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        task = asyncio.create_task(self.clock_on_lcd())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

        await asyncio.gather(*self.tasks)

        await self.done.wait()

    async def start(self) -> None:
        """
        Start the deck
        """

        try:
            while True:
                try:
                    await self.listen()
                except TransportError:
                    logger.info("Lost connection to Stream Deck")

                    self.deck = None

                    for task in self.tasks:
                        task.cancel()

                    try:
                        await self.stop()
                    except Exception:  # pylint: disable=W0718
                        logger.error("Oops stopping", exc_info=True)
                except Exception:  # pylint: disable=W0718
                    logger.error("Oops", exc_info=True)

                logger.info("Waiting 10 seconds to reconnect to Stream Deck.")
                await asyncio.sleep(10)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """
        Stop the deck.
        """
        logger.debug("Stopping the deck")

        for key in self.keys.values():
            try:
                await key.stop()
            except TransportError:
                pass
            except Exception:  # pylint: disable=W0718
                logger.error(f"Error stopping key {key}", exc_info=True)

        for dial in self.dials.values():
            try:
                await dial.stop()
            except TransportError:
                pass
            except Exception:  # pylint: disable=W0718
                logger.error(f"Error stopping dial {dial}", exc_info=True)

        for task in self.animations.values():
            task.cancel()
        self.animations.clear()

        if self.deck:
            self.deck.set_brightness(0)
            self.deck.reset()
            self.deck.close()
            self.deck = None
        await asyncio.sleep(0.5)

    async def render_lcd(self, tile_changed: int | None = None) -> None:
        """
        Render the LCD display.
        """
        if not self.deck:
            return

        image = Image.new(
            "RGBA", (800, 100), self.renderer.get_color("lcd-bg")
        )

        if tile_changed in (1, 2):
            self.status_inhibited = time.time() + 1

        status_bar: bool = time.time() > self.status_inhibited

        for index, dial in self.dials.items():
            mini = status_bar and index in (1, 2)
            tile = await dial.render(mini=mini)
            image.alpha_composite(tile, (index * 220, 0))

        if status_bar:
            time_image = self.renderer.draw_time()
            image.alpha_composite(
                time_image,
                (round(image.width / 2.0 - time_image.width / 2.0), 0),
            )

        image = image.convert("RGB")

        jpg = to_native_touchscreen_tile_format(self.deck, image)

        self.deck.set_touchscreen_image(jpg, 0, 0, 800, 100)

    async def clock_on_lcd(self) -> None:
        """
        Keep writing the time on the LCD display.
        """
        while True:
            next_second = math.ceil(time.time())
            await self.render_lcd()
            await asyncio.sleep(max(0, next_second - time.time()))

    def set_brightness(self, value: int) -> None:
        """
        Set and store Stream Deck brightness.
        """
        self.deck.brightness = min(max(value, 0), 100)
        self.deck.set_brightness(self.deck.brightness)


@define
class BrightnessDial:
    """
    A dial for controlling the Stream Deck backlight brightness.
    """

    dial: int
    controller: DeckController | None = field(init=False, default=None)
    deck: StreamDeck = field(init=False)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start the dial.
        """
        self.deck = deck

    async def stop(self) -> None:
        """
        Stop the dial.
        """
        self.deck = None

        if self.controller is not None:
            await self.controller.render_lcd(tile_changed=self.dial)

    async def render(self, mini: bool = False) -> Image.Image:
        """
        Render the portion of the LCD display (tile) for this dial.
        """
        if not self.deck or self.controller is None:
            return Image.new("RGBA", (140, 100), "#00000000")

        image = await self.controller.renderer.draw_value_dial(
            title="Stream Deck",
            icon="brightness-percent",
            value=self.deck.brightness / 100.0,
            mini=mini,
        )
        return image

    async def on_dial_push(self, dial_state: bool) -> None:
        """
        Called when the dial got pressed or released.
        """

    async def on_dial_turn(self, value: int) -> None:
        """
        Called when the dial got turned.
        """
        if not self.controller:
            return

        change = round(value / abs(value) * (1.6 ** abs(value) - 1))
        self.controller.set_brightness(self.deck.brightness + change)
        await self.controller.render_lcd(tile_changed=self.dial)


def create_touchscreen_tile_image(_deck: StreamDeck) -> Image.Image:
    """
    Create a PIL image for a "tile" of the LCD display.

    A tile is a section of the LCD that corresponds with a encoder dial below
    it, as on the Stream Deck +. The reasonable area that can be used for each
    tile is 140x100, horizontally centered on the dial. With an empty space of
    80 pixels wide between tiles, that makes 140*4 + 80*3 = 800.

    """
    return Image.new("RGB", (140, 100), "black")


def to_native_touchscreen_tile_format(
    deck: StreamDeck, image: Image.Image
) -> bytes:
    """
    Converts a given PIL image to a tile of the native touchscreen format.
    """
    fmt = deck.touchscreen_image_format()
    fmt["size"] = (image.width, image.height)
    return bytes(_to_native_format(image, fmt))


def set_touchscreen_tile_image(
    deck: StreamDeck, image: bytes, tile: int = 0
) -> None:
    """
    Write the tile to part of the touch screen.

    The C{image} is expected to be a JPG bytes object representing a 140x100
    tile of the touchscreen.
    """
    deck.set_touchscreen_image(image, 220 * tile, 0, 140, 100)
