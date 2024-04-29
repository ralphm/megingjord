"""
Google Meet support.
"""

from __future__ import annotations

import asyncio
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

MUTE_ICON_COLOR = {
    ("mic", False): ("microphone", "#666666"),
    ("mic", True): ("microphone-off", "#990000"),
    ("camera", False): ("video-outline", "#666666"),
    ("camera", True): ("video-off-outline", "#990000"),
    ("hand", False): ("hand-back-right-outline", "#336699"),
    ("hand", True): ("hand-back-right-outline", "#666666"),
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

        tile = await self.controller.draw_tile(self.control, color, icon)
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
class GoogleMeetLeaveKey:
    """
    Google Meet Leave Key.
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

        if self.controller:
            tile = await self.controller.draw_tile(
                "Leave call", "#990000", "phone-hangup"
            )
            self.deck.set_key_image(self.key, tile)

    async def stop(self) -> None:
        """
        Stop this key.
        """
        self.deck.set_key_image(self.key, None)

    async def on_key_change(self, key_state: bool) -> None:
        """
        Called when the key got pressed or released.
        """
        if not key_state:
            return

        await self.meet.send_event({"event": "leaveCall"})


@define
class GoogleMeetCoordinator:
    """
    Coordinator for Google Meet calls.
    """

    app: web.Application
    deck_controller: DeckController
    keys: dict[int, str]

    socket: web.WebSocketResponse | None = field(init=False, default=None)
    states: dict[str, bool] = field(init=False, factory=dict)
    control_keys: dict[str, Key] = field(init=False, factory=dict)
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

    async def register_keys(self) -> None:
        """
        Register keys with Stream Deck.
        """
        control_key: GoogleMeetLeaveKey | GoogleMeetMuteKey
        for key, control in self.keys.items():
            if control == "hangup":
                control_key = GoogleMeetLeaveKey(key, self, control)
            else:
                control_key = GoogleMeetMuteKey(key, self, control)
            self.deck_controller.register_key(control_key)
            self.control_keys[control] = control_key
            asyncio.create_task(control_key.start(self.deck_controller.deck))

    async def unregister_keys(self) -> None:
        """
        Unregister keys with Stream Deck.
        """
        for control_key in self.control_keys.values():
            self.deck_controller.unregister_key(control_key)
            await control_key.stop()

        self.control_keys = {}

    async def handle_event(self, event: dict[str, Any]) -> None:
        """
        Handle incoming event.
        """
        if match := RE_MUTED_STATE.match(event["event"]):
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

        await self.register_keys()

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

        return ws

    async def send_event(self, event: dict[str, Any]) -> None:
        """
        Send event to browser extension.
        """
        if self.socket is None:
            raise BrokenPipeError("No active connection from the browser.")

        await self.socket.send_str(json.dumps(event))
