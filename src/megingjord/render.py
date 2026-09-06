# SPDX-License-Identifier: MIT

"""
Rendering blocks for key tiles, LCD dial tiles and the status bar.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from aiohttp import web
from attrs import define
from PIL import Image, ImageDraw, ImageFont

from .icon import svg_icon, svg_to_image

UBUNTU_FONT = Path("/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf")

# LCD dial tile size.
DIAL_TILE_SIZE = (140, 100)


def wrap_text(title: str, width: int, max_lines: int) -> list[str]:
    """
    Wrap a title to at most max_lines lines of the given width.

    Excess text is truncated with an ellipsis on the last line.
    """
    lines = textwrap.wrap(title, width=width)
    if len(lines) > max_lines:
        rest = " ".join(lines[max_lines - 1 :])
        lines = lines[: max_lines - 1]
        lines.append(textwrap.shorten(rest, width=width, placeholder="…"))
    return lines


def draw_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    pos: tuple[float, float],
    font_size: int,
    anchor: str,
    color: str,
) -> None:
    """
    Draw text on an image.
    """
    font = ImageFont.truetype(UBUNTU_FONT, font_size)
    draw.text(pos, text=text, font=font, anchor=anchor, fill=color)


@define
class Renderer:
    """
    Renders key tiles, LCD dial tiles and the status bar.
    """

    app: web.Application

    def get_color(
        self, color_name: str, overrides: dict[str, str] | None = None
    ) -> str:
        """
        Get color from current theme.
        """
        colors: dict[str, str] = self.app["colors"]

        if overrides and color_name in overrides:
            color_name = overrides[color_name]
            if color_name in colors:
                return colors[color_name]
            return color_name

        return colors[color_name]

    async def draw_icon(self, icon: str, color: str, size: int) -> Image.Image:
        """
        Draw an SVG icon into a PIL Image.
        """
        svg = await svg_icon(icon=icon, color=color, size=size)
        return svg_to_image(svg, width=size, height=size)

    async def _draw_icon_at(
        self,
        image: Image.Image,
        icon: str,
        color: str,
        size: int,
        pos: tuple[int, int],
    ) -> None:
        """
        Draw an icon onto an image at the given position.
        """
        icon_image = await self.draw_icon(icon, color, size)
        image.alpha_composite(icon_image, pos)

    async def draw_state_tile(
        self,
        title: str,
        colors: dict[str, str] | None = None,  # color overrides
        icon: str | None = None,
        subtitle: str | None = None,
        badge: str | None = None,
    ) -> bytes:
        """
        Draw a key tile showing the current state.
        """
        return await self._draw_tile(
            title, colors, icon, None, subtitle, badge
        )

    async def draw_transition_tile(
        self,
        title: str,
        colors: dict[str, str] | None = None,  # color overrides
        primary_icon: str | None = None,
        secondary_icon: str | None = None,
        subtitle: str | None = None,
        badge: str | None = None,
    ) -> bytes:
        """
        Draw a key tile showing the current state and the target state.

        The primary icon shows the current state, the smaller secondary icon
        the state that pressing the key will go to.
        """
        return await self._draw_tile(
            title, colors, primary_icon, secondary_icon, subtitle, badge
        )

    async def _draw_tile(
        self,
        title: str,
        colors: dict[str, str] | None,
        primary_icon: str | None,
        secondary_icon: str | None,
        subtitle: str | None,
        badge: str | None,
    ) -> bytes:
        """
        Draw a key tile with a text, and optional icons.

        If there's only a primary icon, it will be rendered large and centered.
        If there's a secondary icon, the primary icon is rendered to the lower
        right, overlapping the smaller icon to the upper left. A badge is
        rendered as a small icon in the top right corner.
        """
        image = Image.new("RGBA", (120, 120))
        draw = ImageDraw.Draw(image)

        def get_color(color_name: str) -> str:
            return self.get_color(color_name, overrides=colors)

        draw.rectangle((0, 0, 119, 119), fill=get_color("tile-bg"))

        if secondary_icon:
            await self._draw_icon_at(
                image,
                secondary_icon,
                get_color("icon-secondary"),
                50,
                (60, 20),
            )

        if primary_icon:
            if secondary_icon:
                size = 70
                pos = (10, 35)
            else:
                size = 80
                pos = (20, 20)

            await self._draw_icon_at(
                image,
                primary_icon,
                get_color("icon-primary"),
                size,
                pos,
            )

        title = textwrap.shorten(title, 15, placeholder="…")
        draw_text(draw, title, (60, 16), 16, "ms", get_color("tile-fg"))

        if subtitle:
            draw_text(
                draw, subtitle, (60, 114), 12, "ms", get_color("tile-fg")
            )

        if badge:
            await self._draw_icon_at(
                image,
                badge,
                get_color("icon-warning"),
                24,
                (80, 22),
            )

        with BytesIO() as jpg:
            image.convert("RGB").save(jpg, format="JPEG", quality=95)
            return jpg.getvalue()

    async def draw_state_dial(
        self, title: str, state: str, icon: str, mini: bool = False
    ) -> Image.Image:
        """
        Draw a dial tile for a state, without a meter.

        The layout matches the value dial: an icon, a title, and the
        state name in the bar label position.
        """
        image = Image.new("RGBA", DIAL_TILE_SIZE, "#00000000")

        draw = ImageDraw.Draw(image)

        margin_left = margin_right = 10
        margin_top = margin_bottom = 2
        icon_size = 40

        await self._draw_icon_at(
            image,
            icon,
            self.get_color("dial-icon"),
            icon_size,
            (margin_left, round(image.height / 2.0 + 5)),
        )

        if not mini:
            lines = wrap_text(title, width=12, max_lines=2)
            text = "\n".join(lines)
            draw_text(
                draw,
                text,
                (margin_left, margin_top),
                18,
                "la",
                self.get_color("dial-title"),
            )

        meter_middle = image.height / 4.0 * 3.0
        meter_left = margin_left + icon_size + margin_left
        meter_right = image.width - margin_right - 1

        if mini:
            label_x = meter_left
            anchor = "ld"
        else:
            label_x = meter_right
            anchor = "rd"

        text = textwrap.shorten(state, width=12, placeholder="…")
        draw_text(
            draw,
            text,
            (label_x, meter_middle - margin_bottom),
            14,
            anchor,
            self.get_color("dial-bar-label"),
        )

        return image

    async def draw_selection_dial(
        self, view: Any, mini: bool = False
    ) -> Image.Image:
        """
        Draw a dial tile with a scroller of selectable items.
        """
        image = Image.new("RGBA", DIAL_TILE_SIZE, "#00000000")

        draw = ImageDraw.Draw(image)

        margin_left = margin_right = 10
        margin_top = 2
        icon_size_active = 40
        icon_size_inactive = 20

        if view.selected > 0:
            item = view.items[view.selected - 1]
            color = self.get_color(
                "icon-active" if item.current else "icon-inactive"
            )
            await self._draw_icon_at(
                image,
                item.icon,
                color,
                icon_size_inactive,
                (
                    margin_left,
                    round(
                        image.height / 2.0
                        + 5
                        + (icon_size_active - icon_size_inactive) / 2.0
                    ),
                ),
            )

        if view.selected < len(view.items) - 1:
            item = view.items[view.selected + 1]
            color = self.get_color(
                "icon-active" if item.current else "icon-inactive"
            )
            await self._draw_icon_at(
                image,
                item.icon,
                color,
                icon_size_inactive,
                (
                    image.width - margin_right - icon_size_inactive,
                    round(
                        image.height / 2.0
                        + 5
                        + (icon_size_active - icon_size_inactive) / 2.0
                    ),
                ),
            )

        item = view.items[view.selected]
        color = self.get_color("icon-active" if item.current else "dial-icon")
        await self._draw_icon_at(
            image,
            item.icon,
            color,
            icon_size_active,
            (
                round((image.width - icon_size_active) / 2.0),
                round(image.height / 2.0 + 5),
            ),
        )

        if not mini:
            text = textwrap.shorten(item.title, width=15, placeholder="…")
            draw_text(
                draw,
                text,
                (round(image.width / 2.0), margin_top),
                18,
                "ma",
                self.get_color("dial-title"),
            )

            if item.subtitle:
                text = textwrap.shorten(
                    item.subtitle, width=22, placeholder="…"
                )
                draw_text(
                    draw,
                    text,
                    (round(image.width / 2.0), margin_top + 20),
                    14,
                    "ma",
                    self.get_color("dial-title"),
                )

        return image

    async def draw_value_dial(
        self,
        title: str,
        icon: str,
        value: float,
        mini: bool = False,
    ) -> Image.Image:
        """
        Draw a dial tile with a meter for a numeric value.
        """
        image = Image.new("RGBA", DIAL_TILE_SIZE, "#00000000")

        draw = ImageDraw.Draw(image)

        margin_left = margin_right = 10
        margin_top = margin_bottom = 2
        icon_size = 40

        await self._draw_icon_at(
            image,
            icon,
            self.get_color("dial-icon"),
            icon_size,
            (margin_left, round(image.height / 2.0 + 5)),
        )

        if not mini:
            lines = wrap_text(title, width=12, max_lines=2)
            text = "\n".join(lines)
            draw_text(
                draw,
                text,
                (margin_left, margin_top),
                18,
                "la",
                self.get_color("dial-title"),
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

        draw_text(
            draw,
            text,
            (label_x, meter_middle - margin_bottom),
            14,
            anchor,
            self.get_color("dial-bar-label"),
        )

        bar_top = meter_middle + margin_top
        bar_bottom = bar_top + 5

        draw.rounded_rectangle(
            (meter_left, bar_top, meter_right, bar_bottom),
            radius=3,
            fill=self.get_color("dial-bar-bg"),
        )

        if value:
            draw.rounded_rectangle(
                (
                    meter_left,
                    bar_top,
                    round(value * (meter_right - meter_left) + meter_left),
                    bar_bottom,
                ),
                radius=3,
                fill=self.get_color("dial-bar-fill"),
            )

        return image

    def draw_time(self) -> Image.Image:
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
            fill=self.get_color("status-bar-bg"),
            outline=self.get_color("status-bar-border"),
            width=1,
            corners=(False, False, True, True),
        )

        dt = datetime.now()
        date_str = f"{dt:%A} {dt.day} {dt:%b}"
        time_str = f"{dt:%H}:{dt:%M}:{dt:%S}"

        draw_text(
            draw,
            date_str,
            (110, 24),
            22,
            "mm",
            self.get_color("status-bar-fg"),
        )

        draw_text(
            draw,
            time_str,
            (330, 24),
            40,
            "mm",
            self.get_color("status-bar-fg"),
        )

        return image
