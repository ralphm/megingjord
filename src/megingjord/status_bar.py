# SPDX-License-Identifier: MIT

"""
Status bar support.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from attrs import define, field
from PIL import Image, ImageDraw, ImageFont

from .render import UBUNTU_FONT, Renderer, draw_text, shorten_to_width

if TYPE_CHECKING:
    from .streamdeck import DeckController

# Status bar banner size.
STATUS_BAR_SIZE = (440, 50)

# Right edge of the clock, keeping a small margin from the banner edge.
CLOCK_RIGHT = 430


def format_countdown(seconds: float) -> str:
    """
    Format a number of seconds as a countdown.
    """
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@define
class Notification:
    """
    A live notification shown on the status bar.

    The countdown is rendered from C{countdown_to} at render time, so
    the notification does not need periodic updates.
    """

    key: str
    title: str
    countdown_to: float | None = None
    icon: str | None = None


def draw_clock(
    draw: ImageDraw.ImageDraw,
    renderer: Renderer,
    condensed: bool = False,
) -> None:
    """
    Draw the current date and time on the status bar.

    The clock is right-aligned and stacked: time above, date below.
    In condensed form the clock is smaller, leaving room for a
    notification card.
    """
    dt = datetime.now()
    time_str = f"{dt:%H}:{dt:%M}:{dt:%S}"
    if condensed:
        date_str = f"{dt:%a} {dt.day} {dt:%b}"
        time_size, date_size = 24, 12
    else:
        date_str = f"{dt:%A} {dt.day} {dt:%b}"
        time_size, date_size = 32, 16

    time_font = ImageFont.truetype(UBUNTU_FONT, time_size)
    time_width = draw.textlength(time_str, font=time_font)
    draw_text(
        draw,
        time_str,
        (CLOCK_RIGHT - time_width / 2, 17),
        time_size,
        "mm",
        renderer.get_color("status-bar-fg"),
    )

    date_font = ImageFont.truetype(UBUNTU_FONT, date_size)
    date_width = draw.textlength(date_str, font=date_font)
    draw_text(
        draw,
        date_str,
        (CLOCK_RIGHT - date_width / 2, 40),
        date_size,
        "mm",
        renderer.get_color("status-bar-fg"),
    )


@define
class StatusBar:
    """
    Status bar showing the clock and live notifications.

    The clock is drawn large when no notifications are active, and
    condenses to the right when a notification card is shown.
    """

    controller: DeckController | None = field(init=False, default=None)
    notifications: dict[str, Notification] = field(init=False, factory=dict)

    def show(self, notification: Notification) -> None:
        """
        Show or update a notification.
        """
        self.notifications[notification.key] = notification

    def clear(self, key: str) -> None:
        """
        Remove a notification.
        """
        self.notifications.pop(key, None)

    async def start(self) -> None:
        """
        Start the status bar.
        """

    async def stop(self) -> None:
        """
        Stop the status bar.
        """

    async def render(self) -> Image.Image:
        """
        Render the status bar.
        """
        if self.controller is None:
            return Image.new("RGBA", STATUS_BAR_SIZE)

        image = Image.new("RGBA", STATUS_BAR_SIZE)
        draw = ImageDraw.Draw(image)
        renderer = self.controller.renderer

        draw.rounded_rectangle(
            (0, -1, image.width - 1, 49),
            radius=16,
            fill=renderer.get_color("status-bar-bg"),
            outline=renderer.get_color("status-bar-border"),
            width=1,
            corners=(False, False, True, True),
        )

        if self.notifications:
            notification = next(iter(self.notifications.values()))
            await self._draw_notification(image, draw, renderer, notification)
            draw_clock(draw, renderer, condensed=True)
        else:
            draw_clock(draw, renderer)

        return image

    async def _draw_notification(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        renderer: Renderer,
        notification: Notification,
    ) -> None:
        """
        Draw a notification card.

        The card is flush with the top of the banner and holds a
        full-height icon, the title on the first line and a small
        countdown with a clock icon below it.
        """
        draw.rounded_rectangle(
            (10, 0, 310, 48),
            radius=12,
            fill=renderer.get_color("status-bar-card-bg"),
            outline=renderer.get_color("status-bar-card-border"),
            width=1,
        )

        title_left = 68
        if notification.icon is not None:
            icon_image = await renderer.draw_icon(
                notification.icon,
                renderer.get_color("status-bar-fg"),
                48,
            )
            image.alpha_composite(icon_image, (11, 1))

        title = shorten_to_width(draw, notification.title, 18, 232)
        draw_text(
            draw,
            title,
            (title_left, 13),
            18,
            "lm",
            renderer.get_color("status-bar-fg"),
        )

        if notification.countdown_to is not None:
            seconds = notification.countdown_to - time.time()
            clock = await renderer.draw_icon(
                "clock-outline",
                renderer.get_color("status-bar-fg"),
                16,
            )
            image.alpha_composite(clock, (title_left, 29))
            draw_text(
                draw,
                format_countdown(seconds),
                (title_left + 20, 37),
                16,
                "lm",
                renderer.get_color("status-bar-fg"),
            )
