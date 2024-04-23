"""
Streamdeck utilities.
"""

from __future__ import annotations

import asyncio
import logging
import math
import textwrap
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol

from aiohttp import web
from attrs import define, field
from cairosvg import svg2png
from PIL import Image, ImageDraw, ImageFont
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import DialEventType, StreamDeck
from StreamDeck.ImageHelpers.PILHelper import _to_native_format
from StreamDeck.Transport.Transport import TransportError
from svgelements import SVG, Color, Matrix, Rect, Text

from .color_utils import black_or_white, make_triad

logger = logging.getLogger(__name__)


UBUNTU_FONT = Path("/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf")


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
class DeckController:
    """
    Stream Deck controller
    """

    app: web.Application
    icon_path: Path
    deck: StreamDeck = field(init=False, default=None)
    done: asyncio.Event = field(init=False, factory=asyncio.Event)
    keys: dict[int, Key] = field(init=False, factory=dict)
    dials: dict[int, Dial] = field(init=False, factory=dict)

    # Status bar inhibited until this time
    status_inhibited: float = field(init=False, default=0)

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

        tasks = []

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

        tasks.extend(
            [
                asyncio.create_task(key.start(deck))
                for key in self.keys.values()
            ]
        )

        tasks.extend(
            [
                asyncio.create_task(dial.start(deck))
                for dial in self.dials.values()
            ]
        )

        tasks.append(asyncio.create_task(self.clock_on_lcd()))

        await asyncio.gather(*tasks)

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
                    logger.error(
                        "Lost connection to Stream Deck", exc_info=True
                    )
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
            await key.stop()

        self.deck.set_brightness(0)
        self.deck.reset()
        self.deck.close()
        await asyncio.sleep(0.5)

    def draw_tile(  # pylint: disable=R0913
        self,
        title: str,
        background_color: str,
        primary_icon: str | None = None,
        secondary_icon: str | None = None,
        subtitle: str | None = None,
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
                svg_icon(
                    path=self.icon_path / f"{secondary_icon}.svg",
                    color=color_triad[1],
                    size=50,
                    pos_x=10,
                    pos_y=20,
                )
            )

        if primary_icon:
            if secondary_icon:
                size = 70
                pos_x, pos_y = 40, 35
            else:
                size = 80
                pos_x, pos_y = 20, 20

            tile.append(
                svg_icon(
                    path=self.icon_path / f"{primary_icon}.svg",
                    color=color_triad[2],
                    size=size,
                    pos_x=pos_x,
                    pos_y=pos_y,
                )
            )

        title = textwrap.shorten(title, 15, placeholder="…")

        tile.append(
            Text(
                title,
                x=60,
                y=16,
                font_size=16,
                text_anchor="middle",
                font_family="sans",
                fill=text_color,
            )
        )

        if subtitle:
            tile.append(
                Text(
                    subtitle,
                    x=60,
                    y=114,
                    font_size=12,
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

    def draw_icon(self, icon_name: str, color: str, size: int) -> Image.Image:
        """
        Draw an SVG icon into a PIL Image.
        """
        svg = svg_icon(
            self.icon_path / f"{icon_name}.svg", color=color, size=size
        )
        return svg_to_image(svg, width=size, height=size)

    def draw_dial_tile(  # pylint: disable=R0913,R0914
        self,
        title: str,
        icon: str,
        value: float,
        color: str = "white",
        mini: bool = False,
    ) -> Image.Image:
        """
        Draw dial tile for LCD.
        """
        image = Image.new("RGBA", (140, 100), "#000000ff")

        draw = ImageDraw.Draw(image)

        margin_left = margin_right = 10
        margin_top = margin_bottom = 2
        icon_size = 40

        icon_image = self.draw_icon(icon, color, icon_size)
        image.alpha_composite(
            icon_image, (margin_left, round(image.height / 2.0 + 5))
        )

        if not mini:
            font = ImageFont.truetype(UBUNTU_FONT, 18)
            text = "\n".join(textwrap.wrap(title, width=12, placeholder="…"))
            draw.text(
                (margin_left, image.height / 2.0 - margin_bottom),
                text=text,
                font=font,
                anchor="ld",
                fill=color,
            )

        meter_middle = image.height / 4.0 * 3.0
        meter_left = margin_left + icon_size + margin_left
        meter_right = image.width - margin_right - 1

        if mini:
            text = textwrap.shorten(title, width=12, placeholder="…")
            label_x = meter_left
            anchor = "ld"
        else:
            text = f"{round(100*value):3d}%"
            label_x = meter_right
            anchor = "rd"

        font = ImageFont.truetype(UBUNTU_FONT, 14)
        draw.text(
            (label_x, meter_middle - margin_bottom),
            text=text,
            font=font,
            anchor=anchor,
            fill=color,
        )

        bar_top = meter_middle + margin_top
        bar_bottom = bar_top + 5

        draw.rounded_rectangle(
            (
                meter_left,
                bar_top,
                round(value * (meter_right - meter_left) + meter_left),
                bar_bottom,
            ),
            radius=3,
            fill=color,
        )

        draw.rounded_rectangle(
            (meter_left, bar_top, meter_right, bar_bottom),
            radius=3,
            fill=None,
            outline=color,
        )

        return image

    async def render_lcd(self, tile_changed: int | None = None) -> None:
        """
        Render the LCD display.
        """
        image = Image.new("RGBA", (800, 100), "#000000ff")

        if tile_changed in (1, 2):
            self.status_inhibited = time.time() + 1

        status_bar: bool = time.time() > self.status_inhibited

        for index, dial in self.dials.items():
            mini = status_bar and index in (1, 2)
            tile = await dial.render(mini=mini)
            image.alpha_composite(tile, (index * 220, 0))

        if status_bar:
            time_image = draw_time()
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

        image = self.controller.draw_dial_tile(
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


def svg_icon(
    path: Path, color: str, size: int, pos_x: int = 0, pos_y: int = 0
) -> SVG:
    """
    Draw an icon.

    This reads the icon from disk, applies the given color, and applies a
    matrix to scale and position with the given size and coordinates.
    """
    icon = SVG.parse(path, reify=False, width=size, height=size)
    next(iter(icon)).fill = Color(color)

    if pos_x or pos_y:
        icon = icon * Matrix(f"translate({pos_x}, {pos_y})")

    return icon


def svg_to_image(svg: SVG, width: int, height: int) -> Image.Image:
    """
    Convert an SVG to a PIL Image.
    """
    png = BytesIO(
        svg2png(
            bytestring=svg.string_xml().encode("utf-8"),
            output_width=width,
            output_height=height,
        )
    )

    return Image.open(png)


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


def draw_time() -> Image.Image:
    """
    Draw time as a PIL Image.
    """
    image = Image.new(
        "RGBA",
        (440, 50),
    )

    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (0, -1, image.width - 1, 49),
        radius=16,
        fill="#660000c0",
        outline="#990000c0",
        width=1,
        corners=(False, False, True, True),
    )

    dt = datetime.now()
    date_str = f"{dt:%A} {dt.day} {dt:%b}"
    time_str = f"{dt:%H}:{dt:%M}:{dt:%S}"

    font = ImageFont.truetype(UBUNTU_FONT, 22)
    draw.text((110, 24), text=date_str, font=font, anchor="mm", fill="white")

    font = ImageFont.truetype(UBUNTU_FONT, 40)
    draw.text(
        (330, 24),
        text=time_str,
        font=font,
        anchor="mm",
        fill="white",
    )

    return image
