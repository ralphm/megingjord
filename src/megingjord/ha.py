# SPDX-License-Identifier: MIT

"""
Home Assistant support.
"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from datetime import datetime, timezone
from functools import partial
from typing import Any, AsyncIterator, Callable, Coroutine

from aiohttp import web
from attrs import define, field
from hass_client import HomeAssistantClient
from PIL import Image
from StreamDeck.Devices.StreamDeck import StreamDeck

from .color_utils import is_dark, rgb_to_hex, scale_rgb_down
from .streamdeck import DeckController, ScrollerItem, ScrollerView

logger = logging.getLogger(__name__)

# Delay before sending a dial value, coalescing rapid turns into a single
# command so the entity does not step through intermediate values.
SEND_DELAY_SECONDS = 0.1


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
    "cover": "window-open",
    "alarm_control_panel": "shield",
}

# Alarm control panel states, services and icons.
ALARM_FEATURES = {
    "armed_home": 1,
    "armed_away": 2,
    "armed_night": 4,
    "armed_custom_bypass": 16,
    "armed_vacation": 32,
}

ALARM_SERVICES = {
    "disarmed": "alarm_disarm",
    "armed_home": "alarm_arm_home",
    "armed_away": "alarm_arm_away",
    "armed_night": "alarm_arm_night",
    "armed_vacation": "alarm_arm_vacation",
    "armed_custom_bypass": "alarm_arm_custom_bypass",
}

ALARM_ICONS = {
    "disarmed": "shield-off",
    "armed_home": "shield-home",
    "armed_away": "shield-lock",
    "armed_night": "shield-moon",
    "armed_vacation": "shield-airplane",
    "armed_custom_bypass": "security",
    "pending": "shield-outline",
    "triggered": "bell-ring",
}

ALARM_TITLES = {
    "disarmed": "Disarmed",
    "armed_home": "Armed home",
    "armed_away": "Armed away",
    "armed_night": "Armed night",
    "armed_vacation": "Armed vacation",
    "armed_custom_bypass": "Custom bypass",
    "pending": "Pending",
    "arming": "Arming",
    "disarming": "Disarming",
    "triggered": "Triggered",
}

# States during which arming or disarming is in progress.
ALARM_TRANSITIONING = frozenset({"pending", "arming", "disarming"})

# States that are armed.
ALARM_ARMED = frozenset(
    {
        "armed_home",
        "armed_away",
        "armed_night",
        "armed_vacation",
        "armed_custom_bypass",
    }
)

# Colors for the pulsing states.
ALARM_PULSE_COLORS = {
    "pending": "icon-warning",
    "arming": "icon-warning",
    "triggered": "icon-alert",
}

# States that pulse the icon.
ALARM_PULSING = frozenset(ALARM_PULSE_COLORS)


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


def icon_from_range(value: float, range_icons: dict[str, str]) -> str | None:
    """
    Get an icon from a numeric range, like the frontend.
    """
    thresholds = []
    for key, icon in range_icons.items():
        try:
            thresholds.append((float(key), key))
        except ValueError:
            continue
    thresholds.sort()
    if not thresholds:
        return None
    if value < thresholds[0][0]:
        return None
    selected = thresholds[0]
    for threshold, key in thresholds:
        if value >= threshold:
            selected = (threshold, key)
        else:
            break
    return range_icons[selected[1]]


def icon_from_translations(
    state: str | None,
    translations: dict[str, Any] | None,
) -> str | None:
    """
    Get an icon from translation-key icons, like the frontend.
    """
    if not translations:
        return None
    if state and translations.get("state", {}).get(state):
        return str(translations["state"][state])
    if state is not None and translations.get("range"):
        try:
            value = float(state)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            icon = icon_from_range(value, translations["range"])
            if icon is not None:
                return icon
            return translations.get("default")
    return translations.get("default")


def strip_mdi(icon: str) -> str:
    """
    Strip the mdi: prefix from an icon name.
    """
    return icon[4:] if icon.startswith("mdi:") else icon


def get_state_text(state: dict[str, Any]) -> str:
    """
    Get a human-readable state text for an entity.
    """
    domain = state.get("entity_id", "").split(".")[0]
    value: str = state.get("state", "")
    if not isinstance(value, str):
        value = ""

    if value == "unavailable":
        return "Unavailable"

    if domain == "light":
        if value == "on":
            brightness = state.get("attributes", {}).get("brightness")
            if brightness:
                return f"On · {round(brightness / 255 * 100)}%"
            return "On"
        return "Off"

    if domain == "switch":
        return "On" if value == "on" else "Off"

    return value.replace("_", " ").capitalize() if value else ""


@define
class HAWebSocketClient:
    """
    Home Assistant WebSocket client.

    Thin wrapper around L{HomeAssistantClient} from hass_client, adding
    state caching and per-entity subscriptions.
    """

    app: web.Application
    url: str
    token: str
    retry_delay: float = field(default=10.0)

    client: HomeAssistantClient | None = field(init=False, default=None)
    states: dict[str, dict[str, Any]] = field(init=False, factory=dict)
    missing: set[str] = field(init=False, factory=set)
    registry_entries: dict[str, dict[str, Any] | None] = field(
        init=False, factory=dict
    )
    platform_icons: dict[str, dict[str, Any] | None] = field(
        init=False, factory=dict
    )
    component_icons: dict[str, dict[str, Any] | None] = field(
        init=False, factory=dict
    )
    subscribers: dict[
        str, set[Callable[[dict[str, Any] | None], Coroutine[Any, Any, None]]]
    ] = field(init=False, factory=dict)
    tasks: set[asyncio.Task[None]] = field(init=False, factory=set)
    task: asyncio.Task[None] | None = field(init=False, default=None)
    _connected: bool = field(init=False, default=False)

    def __attrs_post_init__(self) -> None:
        self.app.cleanup_ctx.append(self.start)

    @property
    def connected(self) -> bool:
        """
        Whether the client is connected to Home Assistant.
        """
        return self._connected

    async def start(self, _app: web.Application) -> AsyncIterator[None]:
        """
        Start the client.

        The hass_client object requires a running event loop, so it is
        created here rather than at construction time.
        """
        self.client = HomeAssistantClient(self.url, self.token)
        self.task = asyncio.create_task(self.run())

        yield

        self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task
        await self.client.disconnect()

    async def run(self) -> None:
        """
        Connect to Home Assistant, reconnecting as needed.
        """
        while True:
            try:
                await self._connect()
            except Exception:  # pylint: disable=W0718
                logger.error(
                    "Error connecting to Home Assistant", exc_info=True
                )

            await asyncio.sleep(self.retry_delay)

    async def _connect(self) -> None:
        """
        Connect to Home Assistant and process messages until disconnected.
        """
        assert self.client is not None
        await self.client.connect()
        self._connected = True
        logger.info("Connected to Home Assistant")

        # The listener is the message pump: commands only get responses
        # while it is running, so it must be started before sending any.
        listener = asyncio.create_task(self.client.start_listening())
        try:
            for entity_id in self.subscribers:
                await self.client.subscribe_entities(
                    self._on_entity_event, [entity_id]
                )
            await listener
        finally:
            if not listener.done():
                listener.cancel()
                with suppress(asyncio.CancelledError):
                    await listener
            self._connected = False
            self.states = {}
            self.missing.clear()
            self.registry_entries.clear()
            self.platform_icons.clear()
            self.component_icons.clear()
            await self._notify_all()
            logger.info("Disconnected from Home Assistant")

    def _on_entity_event(self, event: dict[str, Any]) -> None:
        """
        Handle a subscribe_entities event.

        The initial states arrive as added events, changes as diffs and
        removals as a list of entity ids.
        """
        for entity_id, state in event.get("a", {}).items():
            self.states[entity_id] = self._expand_state(entity_id, state)
            self.missing.discard(entity_id)
            self._notify(entity_id, self.states[entity_id])

        for entity_id in event.get("r", []):
            self.states.pop(entity_id, None)
            self._notify(entity_id, None)

        for entity_id, diff in event.get("c", {}).items():
            state = self._merge_diff(entity_id, diff)
            self._notify(entity_id, state)

    def _expand_state(
        self, entity_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Expand a compact state to the full state format.
        """
        context = state.get("c")
        if isinstance(context, str):
            context = {"id": context, "parent_id": None, "user_id": None}
        last_changed = self._iso_time(state.get("lc"))
        last_updated = self._iso_time(state.get("lu")) or last_changed
        return {
            "entity_id": entity_id,
            "state": state.get("s"),
            "attributes": state.get("a", {}),
            "context": context,
            "last_changed": last_changed,
            "last_updated": last_updated,
        }

    def _merge_diff(
        self, entity_id: str, diff: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Merge a state change diff into the cached state.
        """
        to_add = diff.get("+")
        to_remove = diff.get("-")

        current = self.states.get(entity_id)
        if current is None:
            if to_add is None:
                return None
            state = self._expand_state(entity_id, to_add)
            self.states[entity_id] = state
            return state

        state = dict(current)
        if to_add is not None:
            if "s" in to_add:
                state["state"] = to_add["s"]
            if "c" in to_add:
                context = to_add["c"]
                if isinstance(context, str):
                    state["context"] = {
                        **state.get("context", {}),
                        "id": context,
                    }
                else:
                    state["context"] = {
                        **state.get("context", {}),
                        **context,
                    }
            if "lc" in to_add:
                state["last_changed"] = state["last_updated"] = self._iso_time(
                    to_add["lc"]
                )
            elif "lu" in to_add:
                state["last_updated"] = self._iso_time(to_add["lu"])
            if "a" in to_add:
                attributes = dict(state.get("attributes", {}))
                attributes.update(to_add["a"])
                state["attributes"] = attributes

        if to_remove is not None and to_remove.get("a"):
            attributes = dict(state.get("attributes", {}))
            for key in to_remove["a"]:
                attributes.pop(key, None)
            state["attributes"] = attributes

        self.states[entity_id] = state
        return state

    def _iso_time(self, timestamp: Any) -> str | None:
        """
        Convert an epoch timestamp to an ISO time string.
        """
        if not isinstance(timestamp, (int, float)):
            return None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

    def _notify(
        self,
        entity_id: str,
        state: dict[str, Any] | None,
    ) -> None:
        """
        Notify the subscribers of an entity of a state change.
        """
        for callback in list(self.subscribers.get(entity_id, ())):
            self._spawn(callback, state, entity_id)

    def _spawn(
        self,
        callback: Callable[[dict[str, Any] | None], Coroutine[Any, Any, None]],
        state: dict[str, Any] | None,
        entity_id: str | None = None,
    ) -> None:
        """
        Run a callback as a task.
        """
        task = asyncio.create_task(callback(state))
        self.tasks.add(task)
        task.add_done_callback(partial(self._on_callback_done, entity_id))

    def _on_callback_done(
        self, entity_id: str | None, task: asyncio.Task[None]
    ) -> None:
        """
        Handle a finished callback task.
        """
        self.tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            if entity_id is None:
                logger.error("Error in state callback", exc_info=error)
            else:
                logger.error(
                    "Error in state callback for %s",
                    entity_id,
                    exc_info=error,
                )

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

        if self._connected:
            self._subscribe_entity(entity_id)

        def unsubscribe() -> None:
            subscribers.discard(callback)

        return unsubscribe

    def _subscribe_entity(self, entity_id: str) -> None:
        """
        Subscribe to state changes for an entity on the server.
        """
        if self.client is None:
            return

        task = asyncio.create_task(self._subscribe_entity_async(entity_id))
        self.tasks.add(task)
        task.add_done_callback(partial(self._on_subscribe_done, entity_id))

    async def _subscribe_entity_async(self, entity_id: str) -> None:
        """
        Subscribe to an entity, marking it missing when it has no state.

        The initial states arrive with the subscription result, so an
        entity without a state after subscribing does not exist.
        """
        assert self.client is not None
        await self.client.subscribe_entities(
            self._on_entity_event, [entity_id]
        )
        if entity_id not in self.states:
            self.missing.add(entity_id)
            self._notify(entity_id, None)

    def _on_subscribe_done(
        self, entity_id: str, task: asyncio.Task[Any]
    ) -> None:
        """
        Handle a finished subscription task.
        """
        self.tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "Failed to subscribe to %s", entity_id, exc_info=error
            )

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        """
        Get the current state for an entity.
        """
        return self.states.get(entity_id)

    def is_missing(self, entity_id: str) -> bool:
        """
        Whether the entity does not exist in Home Assistant.
        """
        return entity_id in self.missing

    async def get_entity_icon(
        self,
        entity_id: str,
        attributes: dict[str, Any],
        icon: str | None = None,
        state: str | None = None,
    ) -> str:
        """
        Get the icon for an entity, from an override, the entity icon,
        translation-key icons or the domain.
        """
        if icon:
            return icon
        entity_icon = attributes.get("icon")
        if isinstance(entity_icon, str) and entity_icon.startswith("mdi:"):
            return strip_mdi(entity_icon)
        translation_icon = await self._get_translation_icon(entity_id, state)
        if translation_icon is not None:
            return strip_mdi(translation_icon)
        component_icon = await self._get_component_icon(
            entity_id, attributes, state
        )
        if component_icon is not None:
            return strip_mdi(component_icon)
        return get_entity_icon(entity_id, attributes)

    async def _get_translation_icon(
        self, entity_id: str, state: str | None
    ) -> str | None:
        """
        Resolve the icon from the integration's translation-key icons.
        """
        if self.client is None:
            return None
        try:
            entry = await self._get_registry_entry(entity_id)
            if entry is None:
                return None
            platform = entry.get("platform")
            translation_key = entry.get("translation_key")
            if not platform or not translation_key:
                return None
            icons = await self._get_platform_icons(platform)
            if icons is None:
                return None
            domain = entity_id.split(".")[0]
            translations = icons.get(domain, {}).get(translation_key)
            return icon_from_translations(state, translations)
        except Exception:  # pylint: disable=W0718
            logger.warning(
                "Failed to resolve icon for %s", entity_id, exc_info=True
            )
            return None

    async def _get_registry_entry(
        self, entity_id: str
    ) -> dict[str, Any] | None:
        """
        Get the entity registry entry, cached per entity.
        """
        if entity_id not in self.registry_entries:
            assert self.client is not None
            self.registry_entries[entity_id] = (
                await self.client.get_entity_registry_entry(entity_id)
            )
        return self.registry_entries[entity_id]

    async def _get_platform_icons(
        self, platform: str
    ) -> dict[str, Any] | None:
        """
        Get the integration's entity icons, cached per platform.
        """
        if platform not in self.platform_icons:
            assert self.client is not None
            result = await self.client.send_command(
                "frontend/get_icons", category="entity", integration=platform
            )
            self.platform_icons[platform] = result.get("resources", {}).get(
                platform
            )
        return self.platform_icons[platform]

    async def _get_component_icon(
        self,
        entity_id: str,
        attributes: dict[str, Any],
        state: str | None,
    ) -> str | None:
        """
        Resolve the icon from the component's device-class icons.
        """
        if self.client is None:
            return None
        try:
            domain = entity_id.split(".")[0]
            icons = await self._get_component_icons(domain)
            if icons is None:
                return None
            device_class = attributes.get("device_class")
            translations = (
                icons.get(device_class)
                if isinstance(device_class, str)
                else None
            )
            if translations is None:
                translations = icons.get("_")
            return icon_from_translations(state, translations)
        except Exception:  # pylint: disable=W0718
            logger.warning(
                "Failed to resolve icon for %s", entity_id, exc_info=True
            )
            return None

    async def _get_component_icons(self, domain: str) -> dict[str, Any] | None:
        """
        Get the component's entity icons, cached per domain.
        """
        if domain not in self.component_icons:
            assert self.client is not None
            result = await self.client.send_command(
                "frontend/get_icons",
                category="entity_component",
                integration=[domain],
            )
            self.component_icons[domain] = result.get("resources", {}).get(
                domain
            )
        return self.component_icons[domain]

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
        if self.client is None:
            raise RuntimeError("Not connected to Home Assistant")

        kwargs: dict[str, Any] = {}
        if entity_id is not None:
            kwargs["target"] = {"entity_id": entity_id}
        if data is not None:
            kwargs["service_data"] = data

        return await self.client.call_service(domain, service, **kwargs)


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
            if self.ha.is_missing(self.entity_id):
                text = "Entity not found"
                icon = "alert-outline"
            else:
                text = "Disconnected"
                icon = "cloud-question-outline"
            subtitle = None
            badge = None
            colors = {
                "icon-primary": "icon-inactive",
                "tile-bg": "tile-inactive-bg",
            }
        else:
            attributes = state.get("attributes", {})
            text = attributes.get("friendly_name", self.entity_id)
            icon = await self.ha.get_entity_icon(
                self.entity_id, attributes, self.icon, state["state"]
            )
            subtitle = get_state_text(state)
            badge = None

            if state["state"] == "unavailable":
                badge = "alert-circle"
                colors = {
                    "icon-primary": "icon-inactive",
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

        tile = await self.controller.draw_tile(
            text, colors, icon, subtitle=subtitle, badge=badge
        )
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
    value: float = field(init=False, default=0.0)
    pending: int = field(init=False, default=0)
    tasks: set[asyncio.Task[None]] = field(init=False, factory=set)
    send_task: asyncio.Task[None] | None = field(init=False, default=None)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this dial.
        """
        self.deck = deck
        self.unsubscribes = [self.ha.subscribe(self.entity_id, self.on_state)]
        state = self.ha.get_state(self.entity_id)
        if state is not None:
            self.value = self._get_value(state)
        await self.render()

    async def stop(self) -> None:
        """
        Stop this dial.
        """
        for unsubscribe in self.unsubscribes:
            unsubscribe()

        if self.send_task is not None:
            self.send_task.cancel()

        if self.controller is not None:
            await self.controller.render_lcd(tile_changed=self.dial)

    async def on_state(self, state: dict[str, Any] | None) -> None:
        """
        The entity state changed.
        """
        if state is not None and self.pending == 0:
            self.value = self._get_value(state)
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
            if self.ha.is_missing(self.entity_id):
                title = "Entity not found"
                icon = "alert-outline"
            else:
                title = "Disconnected"
                icon = "cloud-question-outline"
            value = 0.0
        else:
            attributes = state.get("attributes", {})
            title = attributes.get("friendly_name", self.entity_id)
            icon = await self.ha.get_entity_icon(
                self.entity_id, attributes, state=state["state"]
            )
            value = self.value

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

        change = round(value / abs(value) * (1.6 ** abs(value) - 1))
        self.value = min(max(self.value + change / 100.0, 0.0), 1.0)
        await self.controller.render_lcd(tile_changed=self.dial)
        self._schedule_send()

    def _schedule_send(self) -> None:
        """
        Schedule sending the value, coalescing rapid turns.
        """
        if self.send_task is not None:
            self.send_task.cancel()
        self.send_task = asyncio.create_task(self._send_later())

    async def _send_later(self) -> None:
        """
        Send the value after a short delay.
        """
        try:
            await asyncio.sleep(SEND_DELAY_SECONDS)
        except asyncio.CancelledError:
            return

        self.pending += 1
        try:
            await self._set_value(self.value)
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to set %s", self.entity_id, exc_info=True)
        finally:
            self.pending -= 1

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
            brightness = attributes.get("brightness")
            if brightness is None:
                return 0.0
            return float(brightness) / 255.0

        if domain == "media_player":
            volume = attributes.get("volume_level")
            if volume is None:
                return 0.0
            return float(volume)

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


@define
class HAAlarmTile(HAEntityTile):
    """
    Stream Deck key for arming and disarming an alarm control panel.
    """

    arm_service: str = field(kw_only=True)

    _pulse_text: str = field(init=False, default="")
    _pulse_icon: str = field(init=False, default="")
    _pulse_subtitle: str | None = field(init=False, default=None)
    _pulse_badge: str | None = field(init=False, default=None)
    _pulse_rgb: tuple[int, int, int] = field(init=False, default=(0, 0, 0))
    _pulse_frames: dict[float, bytes] = field(init=False, factory=dict)

    async def on_key_change(self, key_state: bool) -> None:
        """
        Called when the key got pressed or released.
        """
        if not key_state:
            return

        state = self.ha.get_state(self.entity_id)
        if state is None:
            return

        value = state["state"]
        if value == "disarmed":
            service = self.arm_service
        else:
            service = "alarm_disarm"

        try:
            await self.ha.call_service(
                "alarm_control_panel", service, entity_id=self.entity_id
            )
        except Exception:  # pylint: disable=W0718
            logger.error(
                "Failed to %s %s", service, self.entity_id, exc_info=True
            )
            await self.set_tile()

    async def set_tile(self) -> None:
        """
        Draw the tile based on the alarm state.
        """
        if not self.controller or not self.deck:
            return

        state = self.ha.get_state(self.entity_id)

        if state is None:
            text = "Disconnected"
            icon = "cloud-question-outline"
            subtitle = None
            badge = None
            colors = {
                "icon-primary": "icon-inactive",
                "tile-bg": "tile-inactive-bg",
            }
        else:
            attributes = state.get("attributes", {})
            text = attributes.get("friendly_name", self.entity_id)
            value = state["state"]
            icon = await self.ha.get_entity_icon(
                self.entity_id, attributes, self.icon, value
            )
            if icon == DOMAIN_ICONS.get("alarm_control_panel"):
                icon = ALARM_ICONS.get(value, icon)
            subtitle = get_state_text(state)
            badge = None
            if value == "unavailable":
                badge = "alert-circle"
                colors = {
                    "icon-primary": "icon-inactive",
                    "tile-bg": "tile-inactive-bg",
                }
            elif value in ALARM_PULSING:
                colors = {"icon-primary": ALARM_PULSE_COLORS[value]}
            elif value in ALARM_ARMED:
                colors = {"icon-primary": "icon-ok"}
            elif value in ALARM_TRANSITIONING or value == "disarmed":
                colors = {
                    "icon-primary": "icon-inactive",
                    "tile-bg": "tile-inactive-bg",
                }
            else:
                colors = {"icon-primary": "icon-active"}

        logger.debug(f"Setting key {self.key} to icon {icon}: {text!r}")

        self._pulse_text = text
        self._pulse_icon = icon
        self._pulse_subtitle = subtitle
        self._pulse_badge = badge
        tile = await self._draw(colors)
        self.deck.set_key_image(self.key, tile)

        if state is not None and state["state"] in ALARM_PULSING:
            self._start_pulse(ALARM_PULSE_COLORS[state["state"]])
        else:
            self._stop_pulse()

    async def _draw(self, colors: dict[str, str]) -> bytes:
        """
        Draw the tile with the stored text and icon.
        """
        assert self.controller is not None
        return await self.controller.draw_tile(
            self._pulse_text,
            colors,
            self._pulse_icon,
            subtitle=self._pulse_subtitle,
            badge=self._pulse_badge,
        )

    def _start_pulse(self, color_name: str) -> None:
        """
        Start the icon pulse animation.
        """
        if self.controller is None:
            return
        hex_color = self.controller.get_color(color_name)
        self._pulse_rgb = (
            int(hex_color[1:3], 16),
            int(hex_color[3:5], 16),
            int(hex_color[5:7], 16),
        )
        self._pulse_frames.clear()
        self.controller.start_key_animation(self.key, self._render_pulse)

    def _stop_pulse(self) -> None:
        """
        Stop the icon pulse animation.
        """
        if self.controller is not None:
            self.controller.stop_key_animation(self.key)

    async def _render_pulse(self, phase: float) -> bytes:
        """
        Render the tile with a pulsing icon, reusing cached frames.
        """
        alpha = round(self._pulse_alpha(phase), 2)
        frame = self._pulse_frames.get(alpha)
        if frame is None:
            r, g, b = self._pulse_rgb
            colors = {"icon-primary": f"rgba({r}, {g}, {b}, {alpha:.2f})"}
            frame = await self._draw(colors)
            self._pulse_frames[alpha] = frame
        return frame

    def _pulse_alpha(self, phase: float) -> float:
        """
        The icon alpha for the given pulse phase, with ease-in-out.
        """
        t = (phase % (2 * math.pi)) / (2 * math.pi)
        if t < 0.5:
            x = t * 2
        else:
            x = (1 - t) * 2
        eased = x * x * (3 - 2 * x)
        return 0.25 + 0.75 * eased

    async def stop(self) -> None:
        """
        Stop this key.
        """
        self._stop_pulse()
        await super().stop()


@define
class AlarmModeScrollerItem(ScrollerItem):
    """
    L{ScrollerItem} wrapper for an alarm control panel state.
    """

    wrapped: str
    current: bool = False

    @property
    def title(self) -> str:
        return ALARM_TITLES.get(
            self.wrapped, self.wrapped.replace("_", " ").capitalize()
        )

    @property
    def subtitle(self) -> str | None:
        return None

    @property
    def icon(self) -> str:
        return ALARM_ICONS.get(self.wrapped, "shield")


@define
class HAAlarmDial:
    """
    Stream Deck dial for arming and disarming an alarm control panel.
    """

    dial: int
    ha: HAWebSocketClient
    entity_id: str

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)
    unsubscribes: list[Callable[[], None]] = field(init=False, factory=list)
    current_view: ScrollerView | None = field(init=False, default=None)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this dial.
        """
        self.deck = deck
        self.unsubscribes = [self.ha.subscribe(self.entity_id, self.on_state)]
        await self.update_view()
        await self.render()

    async def stop(self) -> None:
        """
        Stop this dial.
        """
        for unsubscribe in self.unsubscribes:
            unsubscribe()

        if self.controller is not None:
            await self.controller.render_lcd(tile_changed=self.dial)

    async def on_state(self, state: dict[str, Any] | None) -> None:
        """
        The alarm state changed.
        """
        await self.update_view()
        if self.controller is not None:
            await self.controller.render_lcd(tile_changed=self.dial)

    async def update_view(self) -> None:
        """
        Rebuild the scroller view for the current state.
        """
        state = self.ha.get_state(self.entity_id)
        if state is not None:
            attributes = state.get("attributes", {})
            supported_features = attributes.get("supported_features", 0)
            value = state["state"]
        else:
            supported_features = 0
            value = None

        previous = self.current_view.selected if self.current_view else 0

        items: list[AlarmModeScrollerItem] = []
        selected = 0
        for mode in ["disarmed", *ALARM_FEATURES]:
            if mode != "disarmed" and not (
                supported_features & ALARM_FEATURES[mode]
            ):
                continue
            items.append(AlarmModeScrollerItem(mode, current=mode == value))
            if mode == value:
                selected = len(items) - 1
        if value not in {item.wrapped for item in items}:
            selected = min(previous, len(items) - 1)
        self.current_view = ScrollerView(items=items, selected=selected)

    async def on_dial_turn(self, value: int) -> None:
        """
        Called when the dial got turned.
        """
        if not self.controller or not self.current_view:
            return

        if value < 0:
            self.current_view.selected = max(
                0, self.current_view.selected + value
            )
        else:
            self.current_view.selected = min(
                len(self.current_view.items) - 1,
                self.current_view.selected + value,
            )

        await self.controller.render_lcd(tile_changed=self.dial)

    async def on_dial_push(self, dial_state: bool) -> None:
        """
        Called when the dial got pressed or released.
        """
        if not dial_state or not self.controller or not self.current_view:
            return

        mode = self.current_view.selected_item.wrapped
        assert isinstance(mode, str)
        service = ALARM_SERVICES[mode]
        state = self.ha.get_state(self.entity_id)
        code_arm_required = bool(
            state and state.get("attributes", {}).get("code_arm_required")
        )
        if mode != "disarmed" and code_arm_required:
            logger.warning("Arming %s requires a code", self.entity_id)
            return

        try:
            await self.ha.call_service(
                "alarm_control_panel", service, entity_id=self.entity_id
            )
        except Exception:  # pylint: disable=W0718
            logger.error(
                "Failed to %s %s", service, self.entity_id, exc_info=True
            )

    async def render(self, mini: bool = False) -> Image.Image:
        """
        Render the portion of the LCD display (tile) for this dial.
        """
        if not self.deck or not self.controller or not self.current_view:
            return Image.new("RGBA", (140, 100), "#00000000")

        return await self.controller.draw_dial_tile_scroller(
            self.current_view, mini=mini
        )
