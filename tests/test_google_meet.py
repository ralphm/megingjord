# SPDX-License-Identifier: MIT

"""
Tests for L{megingjord.google_meet}.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from megingjord.color_utils import get_colors
from megingjord.google_meet import GoogleMeetCoordinator, GoogleMeetTile
from megingjord.streamdeck import DeckController

pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web.NotAppKeyWarning")


def make_tile(
    phase: str | None,
    action: str | None = None,
    phases: dict[str, str] | None = None,
) -> GoogleMeetTile:
    """
    A Google Meet tile with a mocked controller and deck.
    """
    app = web.Application()
    app["colors"] = get_colors("default")
    controller = DeckController(app)
    controller.deck = MagicMock()
    controller.renderer = AsyncMock()
    coordinator = GoogleMeetCoordinator(app)
    coordinator.phase = phase
    tile = GoogleMeetTile(0, coordinator, action=action, phases=phases)
    tile.controller = controller
    tile.deck = MagicMock()
    tile.connected = True
    return tile


class TestGoogleMeetCoordinator:
    """
    Tests for L{megingjord.google_meet.GoogleMeetCoordinator}.
    """

    @pytest.mark.asyncio
    async def test_phase_event_deduped(self) -> None:
        """
        A phase event with the current phase is not broadcast.
        """
        coordinator = GoogleMeetCoordinator(web.Application())
        coordinator.phase = "meeting"
        subscriber = AsyncMock()
        coordinator.subscribers.append(subscriber)
        await coordinator.handle_event({"event": "phase", "phase": "meeting"})
        subscriber.handle_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_phase_event_broadcast_on_change(self) -> None:
        """
        A phase event with a new phase is broadcast.
        """
        coordinator = GoogleMeetCoordinator(web.Application())
        coordinator.phase = "lobby"
        subscriber = AsyncMock()
        coordinator.subscribers.append(subscriber)
        await coordinator.handle_event({"event": "phase", "phase": "meeting"})
        subscriber.handle_event.assert_awaited_once_with(
            {"event": "phase", "phase": "meeting"}
        )

    @pytest.mark.asyncio
    async def test_muted_event_broadcast(self) -> None:
        """
        A muted state event is broadcast to subscribers.
        """
        coordinator = GoogleMeetCoordinator(web.Application())
        subscriber = AsyncMock()
        coordinator.subscribers.append(subscriber)
        await coordinator.handle_event(
            {"event": "micMutedState", "muted": True}
        )
        subscriber.handle_event.assert_awaited_once_with(
            {"event": "micMutedState", "muted": True}
        )

    @pytest.mark.asyncio
    async def test_start_closes_socket_before_cleanup(self) -> None:
        """
        The websocket is closed before the server is cleaned up, so
        shutdown does not wait for the connection to time out.
        """
        coordinator = GoogleMeetCoordinator(web.Application())
        calls: list[str] = []
        coordinator.socket = AsyncMock()
        coordinator.socket.close = AsyncMock(
            side_effect=lambda: calls.append("close")
        )

        with (
            patch("aiohttp.web.AppRunner") as runner_cls,
            patch("aiohttp.web.TCPSite") as site_cls,
        ):
            runner = AsyncMock()
            runner.cleanup = AsyncMock(
                side_effect=lambda: calls.append("cleanup")
            )
            runner_cls.return_value = runner
            site_cls.return_value = AsyncMock()

            async for _ in coordinator.start(coordinator.app):
                pass

        assert calls == ["close", "cleanup"]


class TestGoogleMeetTile:
    """
    Tests for L{megingjord.google_meet.GoogleMeetTile}.
    """

    @pytest.mark.asyncio
    async def test_home_green_room_secondary(self) -> None:
        """
        The home tile uses google-meet-secondary in the Green Room.
        """
        tile = make_tile("green_room", action="home")
        await tile._draw()
        tile.controller.renderer.draw_state_tile.assert_awaited_once_with(
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
        tile = make_tile("meeting", action="home")
        await tile._draw()
        tile.controller.renderer.draw_state_tile.assert_awaited_once_with(
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
        tile = make_tile("green_room", action="hangup")
        await tile._draw()
        tile.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="Leave call",
            subtitle=None,
            colors={
                "tile-bg": "google-meet-hangup-bg",
                "icon-primary": "google-meet-hangup-icon",
            },
            icon="phone-hangup",
        )

    @pytest.mark.asyncio
    async def test_switch_tile(self) -> None:
        """
        The switch tile uses its action key colors and icon.
        """
        tile = make_tile("green_room_switch", action="switch")
        await tile._draw()
        tile.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="Switch here",
            subtitle=None,
            colors={
                "tile-bg": "google-meet-active-bg",
                "icon-primary": "google-meet-active-icon",
            },
            icon="video-switch-outline",
        )

    @pytest.mark.asyncio
    async def test_blank_when_disconnected(self) -> None:
        """
        A tile renders blank while the websocket is not connected.
        """
        tile = make_tile("meeting", action="hangup")
        tile.connected = False
        await tile._draw()
        tile.deck.set_key_image.assert_called_once_with(0, None)
        tile.controller.renderer.draw_state_tile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blank_when_no_action_for_phase(self) -> None:
        """
        A phased tile renders blank when the current phase has no
        action.
        """
        tile = make_tile("lobby", phases={"meeting": "hangup"})
        await tile._draw()
        tile.deck.set_key_image.assert_called_once_with(0, None)
        tile.controller.renderer.draw_state_tile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_phased_tile_action_switches(self) -> None:
        """
        A phased tile uses the action for the current phase.
        """
        tile = make_tile(
            "meeting", phases={"lobby": "home", "meeting": "hangup"}
        )
        await tile._draw()
        tile.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="Leave call",
            subtitle=None,
            colors={
                "tile-bg": "google-meet-hangup-bg",
                "icon-primary": "google-meet-hangup-icon",
            },
            icon="phone-hangup",
        )

    @pytest.mark.asyncio
    async def test_mute_tile_not_ready(self) -> None:
        """
        A mute tile renders the not-ready state until the state
        arrives.
        """
        tile = make_tile("meeting", action="mic")
        await tile._draw()
        tile.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="mic",
            subtitle=None,
            colors={
                "tile-fg": "google-meet-fg",
                "tile-bg": "tile-inactive-bg",
                "icon-primary": "icon-inactive",
            },
            icon="microphone-off",
        )

    @pytest.mark.asyncio
    async def test_mute_tile_state(self) -> None:
        """
        A mute tile renders the muted state.
        """
        tile = make_tile("meeting", action="mic")
        tile.muted = True
        await tile._draw()
        tile.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="mic",
            subtitle=None,
            colors={
                "tile-fg": "google-meet-fg",
                "tile-bg": "google-meet-muted-bg",
                "icon-primary": "google-meet-muted-icon",
            },
            icon="microphone-off",
        )

    @pytest.mark.asyncio
    async def test_phase_event_updates_action(self) -> None:
        """
        A phase event switches the phased tile's action.
        """
        tile = make_tile(
            "lobby", phases={"lobby": "home", "meeting": "hangup"}
        )
        tile.meet.phase = "meeting"
        await tile.handle_event({"event": "phase", "phase": "meeting"})
        assert tile.current_action == "hangup"
        tile.controller.renderer.draw_state_tile.assert_awaited_once_with(
            title="Leave call",
            subtitle=None,
            colors={
                "tile-bg": "google-meet-hangup-bg",
                "icon-primary": "google-meet-hangup-icon",
            },
            icon="phone-hangup",
        )

    @pytest.mark.asyncio
    async def test_muted_event_updates_tile(self) -> None:
        """
        A muted state event updates the matching mute tile.
        """
        tile = make_tile("meeting", action="mic")
        await tile.handle_event({"event": "micMutedState", "muted": True})
        assert tile.muted is True
        tile.controller.renderer.draw_state_tile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_muted_event_ignored_for_other_action(self) -> None:
        """
        A muted state event is ignored by tiles with another action.
        """
        tile = make_tile("meeting", action="hangup")
        await tile.handle_event({"event": "micMutedState", "muted": True})
        assert tile.muted is None
        tile.controller.renderer.draw_state_tile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enter_ready_event(self) -> None:
        """
        The enterReady event updates the enter tile.
        """
        tile = make_tile("green_room", action="enter")
        await tile.handle_event({"event": "enterReady", "ready": False})
        assert tile.ready is False

    @pytest.mark.asyncio
    async def test_has_next_meeting_event(self) -> None:
        """
        The hasNextMeeting event updates the start-next tile.
        """
        tile = make_tile("lobby", action="start-next")
        await tile.handle_event(
            {"event": "hasNextMeeting", "hasNextMeeting": False}
        )
        assert tile.available is False

    @pytest.mark.asyncio
    async def test_key_press_sends_event(self) -> None:
        """
        Pressing an action tile sends its event to the extension.
        """
        tile = make_tile("meeting", action="hangup")
        with patch.object(
            GoogleMeetCoordinator, "send_event", new=AsyncMock()
        ) as send:
            await tile.on_key_change(True)
        send.assert_awaited_once_with({"event": "leaveCall"})

    @pytest.mark.asyncio
    async def test_key_press_blank_ignored(self) -> None:
        """
        Pressing a blank tile does nothing.
        """
        tile = make_tile("lobby", phases={"meeting": "hangup"})
        with patch.object(
            GoogleMeetCoordinator, "send_event", new=AsyncMock()
        ) as send:
            await tile.on_key_change(True)
        send.assert_not_awaited()
