"""
Philips Hue support.
"""

import asyncio
import logging
from typing import AsyncGenerator, Callable

from aiohttp import web
from aiohue import HueBridgeV2
from aiohue.v2.controllers.events import EventType
from aiohue.v2.models.light import Light
from attrs import define, field
from StreamDeck.Devices.StreamDeck import StreamDeck

from .color_utils import rgb_to_hex, scale_rgb_tuple, xyb_to_rgb
from .streamdeck import DeckController

logger = logging.getLogger(__name__)


@define
class HueCoordinator:
    """
    Philips Hue Coordinator.

    Continues to spawn L{HueBridgeV2} objects until shutdown.
    """

    app: web.Application
    host: str
    app_key: str

    bridge: HueBridgeV2 | None = field(init=False, default=None)
    done: asyncio.Event = field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        self.app.cleanup_ctx.append(self.start)

    async def get_bridge(self) -> HueBridgeV2:
        """
        Return the current Bridge object.
        """
        if self.bridge:
            return self.bridge

        self.bridge = HueBridgeV2(self.host, self.app_key)
        await self.bridge.initialize()

        return self.bridge

    async def start(self, _app: web.Application) -> AsyncGenerator:
        """
        Start the coordinator.
        """
        bridge = await self.get_bridge()

        yield

        await bridge.close()


@define
class HueLightToggleKey:
    """
    Stream Deck key for toggling a single Hue light.
    """

    key: int
    hue: HueCoordinator
    light_id: str

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)
    unsubscribes: list[Callable] = field(init=False, factory=list)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this key.
        """
        self.deck = deck
        bridge = await self.hue.get_bridge()
        self.unsubscribes = [
            bridge.events.subscribe(
                self.on_hue_connected,
                (EventType.CONNECTED, EventType.DISCONNECTED),
            ),
            bridge.events.subscribe(
                self.on_hue_disconnected, EventType.DISCONNECTED
            ),
            bridge.lights.subscribe(
                self.on_hue_light, id_filter=self.light_id
            ),
        ]
        await self.set_tile_to_light(light_id=self.light_id)

    async def stop(self) -> None:
        """
        Stop this key.
        """
        self.deck.set_key_image(self.key, None)

        for unsubscribe in self.unsubscribes:
            unsubscribe()

    async def on_hue_connected(
        self, _event_type: EventType, _event: dict | None = None
    ) -> None:
        """
        The Hue bridge was connected.
        """
        await self.set_tile_to_light(light_id=self.light_id)

    async def on_hue_disconnected(
        self, _event_type: EventType, _event: dict | None = None
    ) -> None:
        """
        The Hue bridge was disconnected.
        """
        await self.set_tile_to_light()

    async def on_key_change(self, key_state: bool) -> None:
        """
        The Stream Deck key was pressed or released.
        """
        # Ignore key release
        if not key_state:
            return

        try:
            bridge = await self.hue.get_bridge()

            light = bridge.lights[self.light_id]
            await bridge.lights.set_state(self.light_id, on=not light.on.on)
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to set light state", exc_info=True)
            await self.set_tile_to_light(light_id=self.light_id)

    async def set_tile_to_light(
        self, light: Light | None = None, light_id: str | None = None
    ) -> None:
        """
        Draw tile based on passed Hue light object.

        If C{light_id} is passed, the corresponding light object is retrieved
        from the bridge and the C{light} argument is ignored.
        """
        if not self.controller:
            return

        try:
            bridge = await self.hue.get_bridge()

            if light_id:
                light = bridge.lights.get(self.light_id)

            if not light:
                text = "Disconnected"
                icon = "lightbulb-question-outline"
                color = "#330000"
            else:
                device = bridge.lights.get_device(self.light_id)
                text = device.metadata.name

                if light.is_on:
                    icon = "lightbulb-outline"
                    if light.supports_color:
                        x, y = light.color.xy.x, light.color.xy.y
                        brightness = light.brightness / 100.0
                        rgb = xyb_to_rgb(x, y, brightness)
                        scaled_rgb = scale_rgb_tuple(rgb, down=False)
                        color = rgb_to_hex(scaled_rgb)
                        logger.debug(f"  color: {rgb} {scaled_rgb} {color}")
                    else:
                        color = "#996633"
                else:
                    icon = "lightbulb-off-outline"
                    color = "#330000"
        except Exception:  # pylint: disable=W0718
            logger.error("Couldn't get light information", exc_info=True)
            text = "Error"
            icon = "lightbulb-alert-outline"
            color = "#330000"

        logger.debug(f"Setting key {self.key} to icon {icon}: {text!r}")
        tile = self.controller.draw_tile(text, color, icon)

        self.deck.set_key_image(self.key, tile)

    async def on_hue_light(self, _: EventType, light=None) -> None:
        """
        A Hue event was received.
        """
        if light.id != self.light_id:
            logger.debug(f"Ignoring light {light}")
            return

        await self.set_tile_to_light(light)
