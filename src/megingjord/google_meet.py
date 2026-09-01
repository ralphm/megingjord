# SPDX-License-Identifier: MIT

"""
Google Meet support.
"""

from __future__ import annotations

import json
import logging
import pprint
import re
from typing import Any, AsyncIterator

import aiohttp
from aiohttp import WSCloseCode, web
from attrs import define, field
from StreamDeck.Devices.StreamDeck import StreamDeck

from .streamdeck import DeckController, Key

logger = logging.getLogger(__name__)

RE_MUTED_STATE = re.compile(r"^(.*)MutedState$")

MUTE_CONTROLS = ("mic", "camera", "hand")

MUTE_ICON_COLOR = {
    ("mic", False): ("microphone", "google-meet-unmuted"),
    ("mic", True): ("microphone-off", "google-meet-muted"),
    ("camera", False): ("video-outline", "google-meet-unmuted"),
    ("camera", True): ("video-off-outline", "google-meet-muted"),
    ("hand", False): ("hand-back-right-outline", "google-meet-active"),
    ("hand", True): ("hand-back-right-outline", "google-meet-inactive"),
}

# Control name -> (event, title, icon, color)
ACTION_KEYS = {
    "start-instant": (
        "startInstantMeeting",
        "Start instant",
        "video-plus",
        "google-meet-active",
    ),
    "start-next": (
        "startNextMeeting",
        "Start next",
        "calendar-plus",
        "google-meet-active",
    ),
    "enter": ("enterMeeting", "Join now", "login", "google-meet-active"),
    "home": ("returnHome", "Home", "home", "google-meet-inactive"),
    "rejoin": ("rejoin", "Rejoin", "replay", "google-meet-active"),
    "hangup": (
        "leaveCall",
        "Leave call",
        "phone-hangup",
        "google-meet-hangup",
    ),
}


@define
class GoogleMeetMuteKey:
    """
    Google Meet Mute Key.
    """

    key: int
    meet: GoogleMeetCoordinator
    control: str

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this key.
        """
        self.deck = deck

    async def stop(self) -> None:
        """
        Stop this key.
        """
        self.deck.set_key_image(self.key, None)

    async def on_state(self, muted: bool) -> None:
        """
        Received mute state.
        """
        if not self.controller or not self.deck:
            return

        icon, color = MUTE_ICON_COLOR[(self.control, muted)]

        tile = await self.controller.draw_tile(
            title=self.control,
            colors={
                "tile-fg": "google-meet-fg",
                "tile-bg": f"{color}-bg",
                "icon-primary": f"{color}-icon",
            },
            primary_icon=icon,
        )
        self.deck.set_key_image(self.key, tile)

    async def on_key_change(self, key_state: bool) -> None:
        """
        Called when the key got pressed or released.
        """
        if not key_state:
            return

        await self.meet.send_event(
            {"event": f"toggle{self.control.capitalize()}"}
        )


@define
class GoogleMeetActionKey:
    """
    Google Meet Action Key.

    A key with a fixed tile that sends an event to the browser extension
    when pressed, e.g. starting a meeting, joining, leaving or returning
    home. Keys that require the Meet UI to be ready (e.g. joining a
    meeting) can be marked not ready, rendering them inactive and ignoring
    presses.
    """

    key: int
    meet: GoogleMeetCoordinator
    control: str
    ready: bool = field(default=True)

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this key.
        """
        self.deck = deck
        await self._draw()

    async def stop(self) -> None:
        """
        Stop this key.
        """
        self.deck.set_key_image(self.key, None)

    async def on_ready(self, ready: bool) -> None:
        """
        Received readiness state.
        """
        if ready == self.ready:
            return

        self.ready = ready
        await self._draw()

    async def _draw(self) -> None:
        """
        Draw the tile.
        """
        if not self.controller or not self.deck:
            return

        event, title, icon, color = ACTION_KEYS[self.control]
        if not self.ready:
            color = "google-meet-inactive"

        tile = await self.controller.draw_tile(
            title=title,
            colors={
                "tile-bg": f"{color}-bg",
                "icon-primary": f"{color}-icon",
            },
            primary_icon=icon,
        )
        self.deck.set_key_image(self.key, tile)

    async def on_key_change(self, key_state: bool) -> None:
        """
        Called when the key got pressed or released.
        """
        if not key_state or not self.ready:
            return

        event, _title, _icon, _color = ACTION_KEYS[self.control]
        await self.meet.send_event({"event": event})


@define
class GoogleMeetCoordinator:
    """
    Coordinator for Google Meet calls.

    The keys shown on the Stream Deck depend on the meeting phase reported
    by the browser extension: lobby, green room, meeting, exit hall, or
    none when no Meet tab is open. Controls can additionally be hidden
    (e.g. no scheduled meeting to start) or marked not ready (e.g. the
    join button not yet clickable).
    """

    app: web.Application
    deck_controller: DeckController
    keys: dict[str, dict[int, str]]

    socket: web.WebSocketResponse | None = field(init=False, default=None)
    states: dict[str, bool] = field(init=False, factory=dict)
    phase: str | None = field(init=False, default=None)
    control_keys: dict[str, Key] = field(init=False, factory=dict)
    hidden_controls: set[str] = field(init=False, factory=set)
    stopping: bool = field(init=False, default=False)

    def __attrs_post_init__(self) -> None:
        self.app.cleanup_ctx.append(self.start)

    async def start(self, app: web.Application) -> AsyncIterator[None]:
        """
        Start the coordinator.
        """

        async def close_sockets(_app: web.Application) -> None:
            logger.info("Cleaning up websockets")
            self.stopping = True  # Stop accepting new connections

            if self.socket is not None:
                await self.socket.close()

        app.on_shutdown.append(close_sockets)

        self.stopping = False
        app.add_routes([web.get("/", self.websocket_handler)])

        yield

    async def _register_control(self, key: int, control: str) -> None:
        """
        Register a single control key.
        """
        if control in MUTE_CONTROLS:
            control_key: Key = GoogleMeetMuteKey(key, self, control)
        else:
            control_key = GoogleMeetActionKey(key, self, control)

        self.deck_controller.register_key(control_key)
        self.control_keys[control] = control_key
        await control_key.start(self.deck_controller.deck)

        # Draw the tile from a state received before the key was registered.
        if control in MUTE_CONTROLS and control in self.states:
            assert isinstance(control_key, GoogleMeetMuteKey)
            await control_key.on_state(self.states[control])

    async def register_phase(self, phase: str) -> None:
        """
        Register the keys for the given phase.
        """
        if phase == self.phase:
            return

        await self.unregister_keys()
        self.phase = phase

        for key, control in self.keys.get(phase, {}).items():
            if control in self.hidden_controls:
                continue
            await self._register_control(key, control)

    async def unregister_keys(self) -> None:
        """
        Unregister keys with Stream Deck.
        """
        for control_key in self.control_keys.values():
            self.deck_controller.unregister_key(control_key)
            await control_key.stop()

        self.control_keys = {}

    async def set_control_visible(self, control: str, visible: bool) -> None:
        """
        Show or hide a control key.
        """
        if visible:
            self.hidden_controls.discard(control)
            if self.phase is not None and control not in self.control_keys:
                for key, name in self.keys.get(self.phase, {}).items():
                    if name == control:
                        await self._register_control(key, control)
                        break
        else:
            self.hidden_controls.add(control)
            if control in self.control_keys:
                control_key = self.control_keys.pop(control)
                self.deck_controller.unregister_key(control_key)
                await control_key.stop()

    async def handle_event(self, event: dict[str, Any]) -> None:
        """
        Handle incoming event.
        """
        if event["event"] == "phase":
            await self.register_phase(event["phase"])
        elif event["event"] == "enterReady":
            if "enter" in self.control_keys:
                key = self.control_keys["enter"]
                assert isinstance(key, GoogleMeetActionKey)
                await key.on_ready(event["ready"])
        elif event["event"] == "hasNextMeeting":
            await self.set_control_visible(
                "start-next", event["hasNextMeeting"]
            )
        elif match := RE_MUTED_STATE.match(event["event"]):
            control = match.group(1)
            self.states[control] = event["muted"]
            if control in self.control_keys:
                key = self.control_keys[control]
                assert isinstance(key, GoogleMeetMuteKey)
                await key.on_state(event["muted"])
        logger.debug(f"Current states:\n{pprint.pformat(self.states)}")

    async def websocket_handler(
        self, request: web.Request
    ) -> web.WebSocketResponse:
        """
        Handle websockets.
        """

        logger.info("Incoming Websocket connection.")

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        if self.socket is not None:
            logger.warning("Already connected. Closing extra connection.")

            await ws.close(
                code=WSCloseCode.POLICY_VIOLATION,
                message="Already connected".encode("utf-8"),
            )
            return ws

        logger.info("Websocket connection established")
        self.socket = ws
        self.states = {}
        self.phase = None

        async for msg in ws:
            logger.debug(f"Received message {msg}")
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    event = json.loads(msg.data)
                    await self.handle_event(event)
                except Exception:  # pylint: disable=W0718
                    logger.error("Error while handling event", exc_info=True)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(
                    "Websocket connection closed with exception",
                    exc_info=ws.exception(),
                )

        logger.info("Websocket connection closed")

        self.socket = None

        await self.unregister_keys()
        self.phase = None

        return ws

    async def send_event(self, event: dict[str, Any]) -> None:
        """
        Send event to browser extension.
        """
        if self.socket is None:
            raise BrokenPipeError("No active connection from the browser.")

        await self.socket.send_str(json.dumps(event))
