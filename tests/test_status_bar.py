# SPDX-License-Identifier: MIT

"""
Tests for L{megingjord.status_bar}.
"""

import time
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from PIL import Image

from megingjord.color_utils import get_colors
from megingjord.status_bar import Notification, StatusBar, format_countdown


def make_bar() -> tuple[StatusBar, MagicMock]:
    """
    Create a status bar with a mocked controller.
    """
    app = web.Application()
    app["colors"] = get_colors("default")
    from megingjord.streamdeck import DeckController

    controller = DeckController(app)
    controller.deck = MagicMock()
    bar = StatusBar()
    bar.controller = controller
    return bar, controller


class TestFormatCountdown:
    """
    Tests for L{megingjord.status_bar.format_countdown}.
    """

    def test_minutes(self) -> None:
        """
        A countdown under an hour shows minutes and seconds.
        """
        assert format_countdown(65) == "01:05"

    def test_hours(self) -> None:
        """
        A countdown over an hour shows hours.
        """
        assert format_countdown(3661) == "1:01:01"

    def test_zero(self) -> None:
        """
        A countdown at zero shows zero.
        """
        assert format_countdown(0) == "00:00"

    def test_negative(self) -> None:
        """
        A negative countdown is clamped to zero.
        """
        assert format_countdown(-10) == "00:00"


class TestStatusBar:
    """
    Tests for L{megingjord.status_bar.StatusBar}.
    """

    @pytest.mark.asyncio
    async def test_render_no_controller(self) -> None:
        """
        Without a controller, an empty image is returned.
        """
        bar = StatusBar()
        image = await bar.render()
        assert isinstance(image, Image.Image)
        assert image.size == (440, 50)

    @pytest.mark.asyncio
    async def test_render_idle(self) -> None:
        """
        Without notifications, the clock is drawn.
        """
        bar, _controller = make_bar()
        image = await bar.render()
        assert image.size == (440, 50)

    @pytest.mark.asyncio
    async def test_render_notification(self) -> None:
        """
        With a notification, the card and clock are drawn.
        """
        bar, _controller = make_bar()
        bar.show(
            Notification(
                key="meeting",
                title="Standup sync",
                countdown_to=time.time() + 300,
                icon="calendar-clock-outline",
            )
        )
        image = await bar.render()
        assert image.size == (440, 50)

    @pytest.mark.asyncio
    async def test_render_notification_title_only(self) -> None:
        """
        A notification without icon or countdown renders.
        """
        bar, _controller = make_bar()
        bar.show(Notification(key="busy", title="Busy until 15:00"))
        image = await bar.render()
        assert image.size == (440, 50)

    def test_show_and_clear(self) -> None:
        """
        Notifications can be shown, updated and cleared.
        """
        bar, _controller = make_bar()
        bar.show(Notification(key="meeting", title="Standup sync"))
        assert "meeting" in bar.notifications
        bar.show(Notification(key="meeting", title="Updated"))
        assert bar.notifications["meeting"].title == "Updated"
        bar.clear("meeting")
        assert bar.notifications == {}
        bar.clear("meeting")

    def test_first_notification_shown(self) -> None:
        """
        The first notification is rendered.
        """
        bar, _controller = make_bar()
        bar.show(Notification(key="a", title="First"))
        bar.show(Notification(key="b", title="Second"))
        assert next(iter(bar.notifications.values())).title == "First"
