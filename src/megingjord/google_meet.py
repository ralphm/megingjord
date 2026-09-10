# SPDX-License-Identifier: MIT

"""
Google Meet support.
"""

from __future__ import annotations

import json
import logging
import pprint
import re
import textwrap
from typing import Any, AsyncIterator

import aiohttp
from aiohttp import WSCloseCode, web
from attrs import define, field
from StreamDeck.Devices.StreamDeck import StreamDeck

from .registry import (
    BuildContext,
    ConfigError,
    build_config,
    register_key_type,
    register_section,
)
from .streamdeck import DeckController

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
        "video-plus-outline",
        "google-meet-secondary",
    ),
    "start-next": (
        "startNextMeeting",
        "Start next",
        "calendar-clock-outline",
        "google-meet-active",
    ),
    "enter": ("enterMeeting", "Join now", "login", "google-meet-active"),
    "switch": (
        "switchHere",
        "Switch here",
        "video-switch-outline",
        "google-meet-active",
    ),
    "home": ("returnHome", "Home", "home", "google-meet-active"),
    "rejoin": ("rejoin", "Rejoin", "replay", "google-meet-secondary"),
    "hangup": (
        "leaveCall",
        "Leave call",
        "phone-hangup",
        "google-meet-hangup",
    ),
}


# Icon shown when a control is unavailable, e.g. no scheduled meeting.
UNAVAILABLE_ICONS = {"start-next": "calendar-remove-outline"}

# Icon shown for mute keys while the state is not yet known.
NOT_READY_ICONS = {
    "mic": "microphone-off",
    "camera": "video-off-outline",
    "hand": "hand-back-right-off-outline",
}


@define
class GoogleMeetTile:
    """
    A Google Meet key tile.

    A tile with a fixed action (``google_meet.tile``) or a per-phase
    action (``google_meet.phased_tile``). The tile renders blank until
    the websocket is connected and, for phased tiles, the current
    phase has an action.
    """

    key: int
    meet: GoogleMeetCoordinator
    action: str | None = None
    phases: dict[str, str] | None = None

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)
    connected: bool = field(init=False, default=False)
    ready: bool = field(init=False, default=True)
    available: bool = field(init=False, default=True)
    title: str | None = field(init=False, default=None)
    subtitle: str | None = field(init=False, default=None)
    muted: bool | None = field(init=False, default=None)

    @property
    def current_action(self) -> str | None:
        """
        The action for the current phase.
        """
        if self.phases is not None:
            phase = self.meet.phase
            if phase is None:
                return None
            return self.phases.get(phase)
        return self.action

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this key.
        """
        self.deck = deck
        self.meet.subscribers.append(self)
        await self._draw()

    async def stop(self) -> None:
        """
        Stop this key.
        """
        if self in self.meet.subscribers:
            self.meet.subscribers.remove(self)
        self.deck.set_key_image(self.key, None)

    async def on_connection(self, connected: bool) -> None:
        """
        Received connection state.
        """
        self.connected = connected
        if not connected:
            self.ready = True
            self.available = True
            self.title = None
            self.subtitle = None
            self.muted = None
        await self._draw()

    async def handle_event(self, event: dict[str, Any]) -> None:
        """
        Received an event from the browser extension.
        """
        action = self.current_action
        if event["event"] == "phase":
            self.ready = True
            self.available = True
            self.title = None
            self.subtitle = None
            self.muted = None
            await self._draw()
            return
        if action is None:
            return
        if event["event"] == "enterReady" and action == "enter":
            self.ready = event["ready"]
            await self._draw()
        elif event["event"] == "enterLabel" and action == "enter":
            self.title = event["label"]
            await self._draw()
        elif event["event"] == "subtitle" and event["control"] == action:
            self.subtitle = event["subtitle"]
            await self._draw()
        elif event["event"] == "hasNextMeeting" and action == "start-next":
            self.available = event["hasNextMeeting"]
            await self._draw()
        elif match := RE_MUTED_STATE.match(event["event"]):
            control = match.group(1)
            if control == action:
                self.muted = event["muted"]
                await self._draw()

    async def _draw(self) -> None:
        """
        Draw the tile.
        """
        if not self.controller or not self.deck:
            return

        action = self.current_action
        if action is None or not self.connected:
            self.deck.set_key_image(self.key, None)
            return

        if action in MUTE_CONTROLS:
            if self.meet.pending or self.muted is None:
                icon = NOT_READY_ICONS[action]
                colors = {
                    "tile-fg": "google-meet-fg",
                    "tile-bg": "tile-inactive-bg",
                    "icon-primary": "icon-inactive",
                }
            else:
                icon, color = MUTE_ICON_COLOR[(action, self.muted)]
                colors = {
                    "tile-fg": "google-meet-fg",
                    "tile-bg": f"{color}-bg",
                    "icon-primary": f"{color}-icon",
                }
            title = action
            subtitle = None
        else:
            _event, title, icon, color = ACTION_KEYS[action]
            if action == "home" and self.meet.phase == "green_room":
                color = "google-meet-secondary"

            if self.title:
                title = self.title

            subtitle = None
            if self.subtitle and self.available:
                subtitle = textwrap.shorten(self.subtitle, 20, placeholder="…")

            if self.meet.pending or not self.ready or not self.available:
                colors = {
                    "tile-bg": "tile-inactive-bg",
                    "icon-primary": "icon-inactive",
                }
                if not self.available:
                    icon = UNAVAILABLE_ICONS.get(action, icon)
            else:
                colors = {
                    "tile-bg": f"{color}-bg",
                    "icon-primary": f"{color}-icon",
                }

        tile = await self.controller.renderer.draw_state_tile(
            title=title,
            subtitle=subtitle,
            colors=colors,
            icon=icon,
        )
        self.deck.set_key_image(self.key, tile)

    async def on_key_change(self, key_state: bool) -> None:
        """
        Called when the key got pressed or released.
        """
        if not key_state:
            return

        action = self.current_action
        if action is None or not self.connected or self.meet.pending:
            return

        if action in MUTE_CONTROLS:
            await self.meet.send_event(
                {"event": f"toggle{action.capitalize()}"}
            )
        else:
            if not self.ready or not self.available:
                return
            event, _title, _icon, _color = ACTION_KEYS[action]
            await self.meet.send_event({"event": event})


@define
class GoogleMeetCoordinator:
    """
    Coordinator for Google Meet calls.

    Owns the websocket server the browser extension connects to, and
    the meeting state (phase, muted states). Tiles subscribe and
    render the state for their action.
    """

    app: web.Application
    host: str = "127.0.0.1"
    port: int = 2394

    socket: web.WebSocketResponse | None = field(init=False, default=None)
    states: dict[str, bool] = field(init=False, factory=dict)
    phase: str | None = field(init=False, default=None)
    pending: bool = field(init=False, default=False)
    stopping: bool = field(init=False, default=False)
    subscribers: list[GoogleMeetTile] = field(init=False, factory=list)
    runner: web.AppRunner | None = field(init=False, default=None)
    site: web.TCPSite | None = field(init=False, default=None)

    def __attrs_post_init__(self) -> None:
        self.app.cleanup_ctx.append(self.start)

    async def start(self, app: web.Application) -> AsyncIterator[None]:
        """
        Start the websocket server.
        """
        server = web.Application()
        server.add_routes([web.get("/", self.websocket_handler)])
        self.runner = web.AppRunner(server)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        logger.info(
            "Google Meet websocket listening on %s:%s", self.host, self.port
        )

        yield

        if self.socket is not None:
            await self.socket.close()
        await self.runner.cleanup()

    async def handle_event(self, event: dict[str, Any]) -> None:
        """
        Handle incoming event.
        """
        if event["event"] == "phase":
            # The extension marks the phase it expects after a command
            # with pending; only broadcast when the phase or its
            # pending state actually changed, so tiles do not reset
            # their state on unrelated events.
            pending = event.get("pending", False)
            if event["phase"] == self.phase and pending == self.pending:
                return
            self.phase = event["phase"]
            self.pending = pending
            # Tiles reset their state on every phase event; wipe the
            # known states so the mute events that follow are
            # broadcast again instead of being deduplicated.
            self.states = {}
        elif match := RE_MUTED_STATE.match(event["event"]):
            control = match.group(1)
            if self.states.get(control) == event["muted"]:
                return
            self.states[control] = event["muted"]

        for subscriber in self.subscribers:
            await subscriber.handle_event(event)

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
        self.pending = False
        for subscriber in self.subscribers:
            await subscriber.on_connection(True)

        async for msg in ws:
            logger.debug(f"Received message {msg}")
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    event = json.loads(msg.data)
                    await self.handle_event(event)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.error("Error while handling event", exc_info=True)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(
                    "Websocket connection closed with exception",
                    exc_info=ws.exception(),
                )

        logger.info("Websocket connection closed")

        self.socket = None
        for subscriber in self.subscribers:
            await subscriber.on_connection(False)

        return ws

    async def send_event(self, event: dict[str, Any]) -> None:
        """
        Send event to browser extension.
        """
        if self.socket is None:
            raise BrokenPipeError("No active connection from the browser.")

        await self.socket.send_str(json.dumps(event))


@define
class GoogleMeetConfig:
    """
    Google Meet coordinator configuration.
    """

    host: str = "127.0.0.1"
    port: int = 2394


@define
class GoogleMeetTileConfig:
    """
    A Google Meet tile with a fixed action.
    """

    action: str


@define
class GoogleMeetPhasedTileConfig:
    """
    A Google Meet tile with a per-phase action.
    """

    phases: dict[str, str]


def _build_google_meet(data: dict[str, Any], context: BuildContext) -> None:
    """
    Build the Google Meet coordinator from its configuration section.
    """
    config = build_config(GoogleMeetConfig, "google_meet", data)
    context.google_meet = GoogleMeetCoordinator(
        context.app, host=config.host, port=config.port
    )


def _build_tile(
    key: int, data: dict[str, Any], context: BuildContext
) -> GoogleMeetTile:
    """
    Build a Google Meet tile with a fixed action.
    """
    if context.google_meet is None:
        raise ConfigError(
            f"keys[{key}]: google_meet.tile requires a google_meet section"
        )
    config = build_config(GoogleMeetTileConfig, f"keys[{key}]", data)
    return GoogleMeetTile(key, context.google_meet, action=config.action)


def _build_phased_tile(
    key: int, data: dict[str, Any], context: BuildContext
) -> GoogleMeetTile:
    """
    Build a Google Meet tile with a per-phase action.
    """
    if context.google_meet is None:
        raise ConfigError(
            f"keys[{key}]: google_meet.phased_tile requires a "
            "google_meet section"
        )
    config = build_config(GoogleMeetPhasedTileConfig, f"keys[{key}]", data)
    return GoogleMeetTile(key, context.google_meet, phases=config.phases)


register_section("google_meet", _build_google_meet)
register_key_type("google_meet.tile", _build_tile)
register_key_type("google_meet.phased_tile", _build_phased_tile)
