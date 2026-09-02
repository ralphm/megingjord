# SPDX-License-Identifier: MIT

"""
Home Assistant support.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, AsyncIterator, Callable, Coroutine

import aiohttp
from aiohttp import web
from attrs import define, field
from PIL import Image
from StreamDeck.Devices.StreamDeck import StreamDeck

from .color_utils import is_dark, rgb_to_hex, scale_rgb_down
from .streamdeck import DeckController

logger = logging.getLogger(__name__)


class HAError(Exception):
    """
    Home Assistant error.
    """


# Icon shown for the off state of known entity types.
OFF_ICONS = {
    "lightbulb": "lightbulb-off-outline",
    "power-plug": "power-plug-off-outline",
}

# Default icons per entity domain.
DOMAIN_ICONS = {
    "light": "lightbulb",
    "switch": "power-plug",
    "media_player": "speaker",
    "number": "numeric",
    "binary_sensor": "toggle-switch",
    "sensor": "numeric",
}


def normalize_url(url: str) -> str:
    """
    Normalize a Home Assistant URL to a WebSocket URL.
    """
    if url.startswith("ws://") or url.startswith("wss://"):
        return url

    if url.startswith("http://"):
        url = "ws://" + url[len("http://") :]
    elif url.startswith("https://"):
        url = "wss://" + url[len("https://") :]
    else:
        raise ValueError(f"Invalid Home Assistant URL: {url}")

    if not url.endswith("/api/websocket"):
        url = url.rstrip("/") + "/api/websocket"

    return url


def get_entity_icon(
    entity_id: str,
    attributes: dict[str, Any],
    icon: str | None = None,
) -> str:
    """
    Get the icon for an entity, from an override, the entity icon or domain.
    """
    if icon:
        return icon

    entity_icon = attributes.get("icon")
    if isinstance(entity_icon, str) and entity_icon.startswith("mdi:"):
        return entity_icon[4:]

    domain = entity_id.split(".")[0]
    return DOMAIN_ICONS.get(domain, "toggle-switch")


@define
class HAWebSocketClient:
    """
    Home Assistant WebSocket client.

    Connects to the Home Assistant WebSocket API, subscribes to state
    changes, and provides helpers for getting state and calling services.
    """

    app: web.Application
    url: str
    token: str

    ws: aiohttp.ClientWebSocketResponse | None = field(
        init=False, default=None
    )
    next_id: int = field(init=False, default=0)
    pending: dict[int, asyncio.Future[Any]] = field(init=False, factory=dict)
    states: dict[str, dict[str, Any]] = field(init=False, factory=dict)
    subscribers: dict[
        str, set[Callable[[dict[str, Any] | None], Coroutine[Any, Any, None]]]
    ] = field(init=False, factory=dict)
    tasks: set[asyncio.Task[None]] = field(init=False, factory=set)
    task: asyncio.Task[None] | None = field(init=False, default=None)

    def __attrs_post_init__(self) -> None:
        self.app.cleanup_ctx.append(self.start)

    @property
    def connected(self) -> bool:
        """
        Whether the client is connected to Home Assistant.
        """
        return self.ws is not None

    async def start(self, _app: web.Application) -> AsyncIterator[None]:
        """
        Start the client.
        """
        self.task = asyncio.create_task(self.run())

        yield

        self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task

    async def run(self) -> None:
        """
        Connect to Home Assistant, reconnecting as needed.
        """
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self._connect(session)
                except Exception:  # pylint: disable=W0718
                    logger.error(
                        "Error connecting to Home Assistant", exc_info=True
                    )

                await asyncio.sleep(10)

    async def _connect(self, session: aiohttp.ClientSession) -> None:
        """
        Connect to Home Assistant and process messages until disconnected.
        """
        ws = await session.ws_connect(self.url)

        try:
            msg = await ws.receive_json()
            if msg["type"] != "auth_required":
                raise HAError(f"Unexpected message: {msg}")

            await ws.send_json({"type": "auth", "access_token": self.token})

            msg = await ws.receive_json()
            if msg["type"] != "auth_ok":
                raise HAError(f"Authentication failed: {msg}")

            logger.info("Connected to Home Assistant")
            self.ws = ws

            await self._get_states()
            await self._subscribe_events()
            await self._read_messages(ws)
        finally:
            self.ws = None
            self.states = {}

            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        HAError("Disconnected from Home Assistant")
                    )
            self.pending = {}

            await self._notify_all()
            logger.info("Disconnected from Home Assistant")

    async def _command(self, command: str, **kwargs: Any) -> Any:
        """
        Send a command and wait for the result.
        """
        if self.ws is None:
            raise HAError("Not connected to Home Assistant")

        self.next_id += 1
        msg_id = self.next_id
        future: asyncio.Future[Any] = (
            asyncio.get_running_loop().create_future()
        )
        self.pending[msg_id] = future

        await self.ws.send_json({"id": msg_id, "type": command, **kwargs})

        return await future

    async def _get_states(self) -> None:
        """
        Fetch all entity states.
        """
        result = await self._command("get_states")
        self.states = {state["entity_id"]: state for state in result}

    async def _subscribe_events(self) -> None:
        """
        Subscribe to state change events.
        """
        await self._command("subscribe_events", event_type="state_changed")

    async def _read_messages(
        self, ws: aiohttp.ClientWebSocketResponse
    ) -> None:
        """
        Process incoming messages.
        """
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning(
                        "Invalid JSON from Home Assistant: %r", msg.data
                    )
                    continue

                if data["type"] == "result":
                    future = self.pending.pop(data["id"], None)
                    if future is not None and not future.done():
                        if data["success"]:
                            future.set_result(data.get("result"))
                        else:
                            error = data.get("error", {})
                            future.set_exception(
                                HAError(error.get("message", "Unknown error"))
                            )
                elif data["type"] == "event":
                    event = data["event"]
                    if event["event_type"] == "state_changed":
                        entity_id = event["data"]["entity_id"]
                        new_state = event["data"].get("new_state")
                        if new_state is None:
                            self.states.pop(entity_id, None)
                        else:
                            self.states[entity_id] = new_state
                        for callback in list(
                            self.subscribers.get(entity_id, ())
                        ):
                            self._spawn(callback, new_state)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("WebSocket error: %s", ws.exception())

    def _spawn(
        self,
        callback: Callable[[dict[str, Any] | None], Coroutine[Any, Any, None]],
        state: dict[str, Any] | None,
    ) -> None:
        """
        Run a callback as a task.
        """
        task = asyncio.create_task(callback(state))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _notify_all(self) -> None:
        """
        Notify all subscribers of a disconnect.
        """
        callbacks: set[
            Callable[[dict[str, Any] | None], Coroutine[Any, Any, None]]
        ] = set()
        for subscribers in self.subscribers.values():
            callbacks.update(subscribers)

        for callback in callbacks:
            self._spawn(callback, None)

    def subscribe(
        self,
        entity_id: str,
        callback: Callable[[dict[str, Any] | None], Coroutine[Any, Any, None]],
    ) -> Callable[[], None]:
        """
        Subscribe to state changes for an entity.

        Returns an unsubscribe function.
        """
        subscribers = self.subscribers.setdefault(entity_id, set())
        subscribers.add(callback)

        def unsubscribe() -> None:
            subscribers.discard(callback)

        return unsubscribe

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        """
        Get the current state for an entity.
        """
        return self.states.get(entity_id)

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """
        Call a Home Assistant service.
        """
        kwargs: dict[str, Any] = {"domain": domain, "service": service}
        if entity_id is not None:
            kwargs["target"] = {"entity_id": entity_id}
        if data is not None:
            kwargs["service_data"] = data

        return await self._command("call_service", **kwargs)


@define
class HAEntityTile:
    """
    Stream Deck key for toggling a Home Assistant entity.
    """

    key: int
    ha: HAWebSocketClient
    entity_id: str
    icon: str | None = field(default=None)

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)
    unsubscribes: list[Callable[[], None]] = field(init=False, factory=list)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this key.
        """
        self.deck = deck
        self.unsubscribes = [self.ha.subscribe(self.entity_id, self.on_state)]
        await self.set_tile()

    async def stop(self) -> None:
        """
        Stop this key.
        """
        for unsubscribe in self.unsubscribes:
            unsubscribe()

        self.deck.set_key_image(self.key, None)

    async def on_state(self, _state: dict[str, Any] | None) -> None:
        """
        The entity state changed.
        """
        await self.set_tile()

    async def on_key_change(self, key_state: bool) -> None:
        """
        Called when the key got pressed or released.
        """
        if not key_state:
            return

        try:
            await self.ha.call_service(
                "homeassistant", "toggle", entity_id=self.entity_id
            )
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to toggle %s", self.entity_id, exc_info=True)
            await self.set_tile()

    async def set_tile(self) -> None:
        """
        Draw the tile based on the entity state.
        """
        if not self.controller or not self.deck:
            return

        state = self.ha.get_state(self.entity_id)

        if state is None:
            text = "Disconnected"
            icon = get_entity_icon(self.entity_id, {})
            colors = {
                "icon-primary": "icon-inactive",
                "tile-bg": "tile-inactive-bg",
            }
        else:
            attributes = state.get("attributes", {})
            text = attributes.get("friendly_name", self.entity_id)
            icon = get_entity_icon(self.entity_id, attributes, self.icon)

            if state["state"] == "unavailable":
                colors = {
                    "icon-primary": "icon-alert",
                    "tile-bg": "tile-inactive-bg",
                }
            elif state["state"] == "on":
                rgb = attributes.get("rgb_color")
                if rgb:
                    rgb = tuple(rgb)
                    color = rgb_to_hex(rgb)
                    colors = {
                        "icon-primary": color,
                        "tile-bg": (
                            "tile-inactive-bg"
                            if is_dark(*scale_rgb_down(rgb))
                            else "tile-bg"
                        ),
                    }
                else:
                    colors = {"icon-primary": "icon-active"}
            else:
                icon = OFF_ICONS.get(icon, icon)
                colors = {
                    "icon-primary": "icon-inactive",
                    "tile-bg": "tile-inactive-bg",
                }

        logger.debug(f"Setting key {self.key} to icon {icon}: {text!r}")

        tile = await self.controller.draw_tile(text, colors, icon)
        self.deck.set_key_image(self.key, tile)


@define
class HAEntityDial:
    """
    Stream Deck dial for controlling a Home Assistant entity.
    """

    dial: int
    ha: HAWebSocketClient
    entity_id: str

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)
    unsubscribes: list[Callable[[], None]] = field(init=False, factory=list)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this dial.
        """
        self.deck = deck
        self.unsubscribes = [self.ha.subscribe(self.entity_id, self.on_state)]
        await self.render()

    async def stop(self) -> None:
        """
        Stop this dial.
        """
        for unsubscribe in self.unsubscribes:
            unsubscribe()

        if self.controller is not None:
            await self.controller.render_lcd(tile_changed=self.dial)

    async def on_state(self, _state: dict[str, Any] | None) -> None:
        """
        The entity state changed.
        """
        if self.controller is not None:
            await self.controller.render_lcd(tile_changed=self.dial)

    async def render(self, mini: bool = False) -> Image.Image:
        """
        Render the portion of the LCD display (tile) for this dial.
        """
        if not self.deck or self.controller is None:
            return Image.new("RGBA", (140, 100), "#00000000")

        state = self.ha.get_state(self.entity_id)

        if state is None:
            title = "Disconnected"
            icon = "cloud-question-outline"
            value = 0.0
        else:
            attributes = state.get("attributes", {})
            title = attributes.get("friendly_name", self.entity_id)
            icon = get_entity_icon(self.entity_id, attributes)
            value = self._get_value(state)

        return await self.controller.draw_dial_tile(
            title=title, icon=icon, value=value, mini=mini
        )

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

        state = self.ha.get_state(self.entity_id)
        if state is None:
            return

        change = round(value / abs(value) * (1.6 ** abs(value) - 1))
        pct = min(max(self._get_value(state) + change / 100.0, 0.0), 1.0)

        try:
            await self._set_value(pct)
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to set %s", self.entity_id, exc_info=True)

        await self.controller.render_lcd(tile_changed=self.dial)

    def _get_value(self, state: dict[str, Any]) -> float:
        """
        Get the current value as a fraction of the range.
        """
        domain = self.entity_id.split(".")[0]
        attributes = state.get("attributes", {})

        if domain == "number":
            try:
                current = float(state["state"])
            except (TypeError, ValueError):
                return 0.0
            minimum = float(attributes.get("min", 0))
            maximum = float(attributes.get("max", 100))
            if maximum <= minimum:
                return 0.0
            return (current - minimum) / (maximum - minimum)

        if domain == "light":
            return float(attributes.get("brightness", 0)) / 255.0

        if domain == "media_player":
            return float(attributes.get("volume_level", 0))

        return 0.0

    async def _set_value(self, pct: float) -> None:
        """
        Set the entity value from a fraction of the range.
        """
        domain = self.entity_id.split(".")[0]
        state = self.ha.get_state(self.entity_id)
        if state is None:
            return

        attributes = state.get("attributes", {})

        if domain == "number":
            minimum = float(attributes.get("min", 0))
            maximum = float(attributes.get("max", 100))
            value = minimum + pct * (maximum - minimum)
            await self.ha.call_service(
                "number",
                "set_value",
                entity_id=self.entity_id,
                data={"value": value},
            )
        elif domain == "light":
            brightness = round(pct * 255)
            if brightness <= 0:
                await self.ha.call_service(
                    "light", "turn_off", entity_id=self.entity_id
                )
            else:
                await self.ha.call_service(
                    "light",
                    "turn_on",
                    entity_id=self.entity_id,
                    data={"brightness": brightness},
                )
        elif domain == "media_player":
            await self.ha.call_service(
                "media_player",
                "volume_set",
                entity_id=self.entity_id,
                data={"volume_level": pct},
            )
