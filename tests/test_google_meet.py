# SPDX-License-Identifier: MIT

"""
Tests for L{megingjord.google_meet}.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from megingjord.color_utils import get_colors
from megingjord.google_meet import GoogleMeetActionKey, GoogleMeetCoordinator
from megingjord.streamdeck import DeckController

pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web.NotAppKeyWarning")


def make_key(phase: str | None, control: str = "home") -> GoogleMeetActionKey:
    """
    A Google Meet action key with a mocked controller and deck.
    """
    app = web.Application()
    app["colors"] = get_colors("default")
    controller = DeckController(app)
    controller.deck = MagicMock()
    controller.renderer = AsyncMock()
    coordinator = GoogleMeetCoordinator(app, controller, {})
    coordinator.phase = phase
    key = GoogleMeetActionKey(0, coordinator, control)
    key.controller = controller
    key.deck = MagicMock()
    return key


class TestGoogleMeetActionKey:
    """
    Tests for L{megingjord.google_meet.GoogleMeetActionKey}.
    """

    @pytest.mark.asyncio
    async def test_home_green_room_secondary(self) -> None:
        """
        The home tile uses google-meet-secondary in the Green Room.
        """
        key = make_key("green room")
        await key._draw()
        key.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="Home",
            subtitle=None,
            colors={
                "tile-bg": "google-meet-secondary-bg",
                "icon-primary": "google-meet-secondary-icon",
            },
            icon="home",
        )

    @pytest.mark.asyncio
    async def test_home_other_phase_active(self) -> None:
        """
        The home tile uses google-meet-active in other phases.
        """
        key = make_key("meeting")
        await key._draw()
        key.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="Home",
            subtitle=None,
            colors={
                "tile-bg": "google-meet-active-bg",
                "icon-primary": "google-meet-active-icon",
            },
            icon="home",
        )

    @pytest.mark.asyncio
    async def test_other_control_green_room_unaffected(self) -> None:
        """
        Other controls keep their color in the Green Room.
        """
        key = make_key("green room", control="hangup")
        await key._draw()
        key.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="Leave call",
            subtitle=None,
            colors={
                "tile-bg": "google-meet-hangup-bg",
                "icon-primary": "google-meet-hangup-icon",
            },
            icon="phone-hangup",
        )
