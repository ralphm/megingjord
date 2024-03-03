"""
Streamdeck utilities.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol

from attrs import define, field
from cairosvg import svg2png
from PIL import Image, ImageDraw, ImageFont
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import StreamDeck
from StreamDeck.ImageHelpers.PILHelper import (create_touchscreen_image,
                                               to_native_touchscreen_format)
from StreamDeck.Transport.Transport import TransportError
from svgelements import SVG, Color, Matrix, Rect, Text

from .color_utils import black_or_white, make_triad

UBUNTU_FONT = Path("/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf")


class Key(Protocol):
    """
    A Stream Deck Key.
    """

    controller: DeckController | None
    deck: StreamDeck
    key: int

    def __init__(self, key: int) -> None:
        ...

    async def start(self) -> None:
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


@define
class DeckController:
    """
    Stream Deck controller
    """

    icon_path: Path
    deck: StreamDeck = field(init=False, default=None)
    done: asyncio.Event = field(init=False, factory=asyncio.Event)
    keys: dict[int, Key] = field(init=False, factory=dict)

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

    async def on_key_change(self, deck, key, key_state):
        """
        Called when the key got pressed or released.
        """
        if deck != self.deck or key not in self.keys:
            return

        await self.keys[key].on_key_change(key_state)

    async def listen(self):
        """
        Find a Stream Deck, open and initialize.
        """
        streamdecks = DeviceManager().enumerate()

        tasks = []

        if not streamdecks:
            return

        self.deck = deck = streamdecks[0]

        deck.open()
        deck.reset()

        deck.set_key_callback_async(self.on_key_change)

        deck.set_brightness(100)

        tasks.extend(
            [
                asyncio.create_task(key.start(deck))
                for key in self.keys.values()
            ]
        )

        tasks.append(asyncio.create_task(self.clock_on_lcd()))

        await asyncio.gather(*tasks)

        with suppress(asyncio.CancelledError):
            await self.done.wait()

    async def start(self):
        """
        Start the deck
        """

        with suppress(asyncio.CancelledError):
            while True:
                listen_task = asyncio.create_task(self.listen())
                try:
                    await listen_task
                except TransportError:
                    logging.error(
                        "Lost connection to Stream Deck", exc_info=True
                    )

                logging.info("Waiting 10 seconds to reconnect to Stream Deck.")
                await asyncio.sleep(10)

        await self.stop()

    async def stop(self):
        """
        Stop the deck.
        """

        for key in self.keys.values():
            await key.stop()

        self.deck.set_brightness(0)
        self.deck.close()
        await asyncio.sleep(2)

    def draw_tile(  # pylint: disable=R0913
        self,
        text: str,
        background_color: str,
        primary_icon: str | None = None,
        secondary_icon: str | None = None,
    ) -> bytes:
        """
        Draw a tile with a text, and optional icons.

        If there's only a primary icon, it will be rendered large and centered.
        If there's a secondary icon, the primary icon is rendered to the lower
        right, overlapping the smaller icon to the upper left. The colors of
        the icons are taken from the background color.

        The text is rendered at the bottom.
        """

        color_triad = make_triad(background_color)
        text_color = black_or_white(background_color)

        tile = SVG(width=120, height=120)

        tile.append(Rect(width=120, height=120, fill=color_triad[0]))

        if secondary_icon:
            tile.append(
                draw_icon(
                    path=self.icon_path / f"{secondary_icon}.svg",
                    color=color_triad[1],
                    size=60,
                    pos_x=5,
                    pos_y=20,
                )
            )

        if primary_icon:
            if secondary_icon:
                size = 80
                pos_x, pos_y = 35, 35
            else:
                size = 90
                pos_x, pos_y = 15, 20

            tile.append(
                draw_icon(
                    path=self.icon_path / f"{primary_icon}.svg",
                    color=color_triad[2],
                    size=size,
                    pos_x=pos_x,
                    pos_y=pos_y,
                )
            )

        tile.append(
            Text(
                text,
                x=60,
                y=16,
                font_size=16,
                text_anchor="middle",
                font_family="sans",
                fill=text_color,
            )
        )

        png = BytesIO(
            svg2png(
                bytestring=tile.string_xml().encode("utf-8"),
                output_width=120,
                output_height=120,
            )
        )

        image = Image.open(png)
        with BytesIO() as jpg:
            image.save(jpg, format="JPEG", quality=95)
            return jpg.getvalue()

    async def clock_on_lcd(self):
        """
        Keep writing the time on the LCD display.
        """
        while True:
            jpg = draw_time(self.deck)
            self.deck.set_touchscreen_image(jpg, 0, 0, 800, 100)
            await asyncio.sleep(0.10)


def draw_icon(
    path: Path, color: str, size: int, pos_x: int, pos_y: int
) -> SVG:
    """
    Draw an icon.

    This reads the icon from disk, applies the given color, and applies a
    matrix to scale and position with the given size and coordinates.
    """
    icon = SVG.parse(path, reify=False, width=size, height=size)
    next(iter(icon)).fill = Color(color)
    return icon * Matrix(f"translate({pos_x}, {pos_y})")


def draw_time(deck):
    """
    Draw time on LCD display.
    """
    image = create_touchscreen_image(deck)
    draw = ImageDraw.Draw(image)

    dt = datetime.now()
    date_str = f"{dt:%A} {dt.day} {dt:%B} {dt.year}"
    time_str = f"{dt:%H}:{dt:%M}"

    middle = deck.TOUCHSCREEN_PIXEL_WIDTH / 2.0
    font = ImageFont.truetype(
        UBUNTU_FONT, 0.18 * deck.TOUCHSCREEN_PIXEL_HEIGHT
    )
    draw.text((middle, 2), text=date_str, font=font, anchor="ma", fill="white")

    font = ImageFont.truetype(UBUNTU_FONT, 64)
    draw.text(
        (middle, deck.TOUCHSCREEN_PIXEL_HEIGHT - 2),
        text=time_str,
        font=font,
        anchor="md",
        fill="white",
    )

    return to_native_touchscreen_format(deck, image)
