"""
Philips Hue support.
"""

import asyncio
import logging
from typing import Callable

from aiohue import HueBridgeV2
from aiohue.v2.controllers.events import EventType
from aiohue.v2.models.light import Light
from attrs import define, field
from StreamDeck.Devices.StreamDeck import StreamDeck

from .color_utils import rgb_to_hex, scale_rgb_tuple, xyb_to_rgb
from .streamdeck import DeckController


async def get_bridge(host: str, app_key: str) -> HueBridgeV2:
    """
    Get a Hue Bridge instance and setup cleanup.
    """

    bridge = HueBridgeV2(host, app_key)

    done = asyncio.Event()

    async def cleanup():
        try:
            await done.wait()
        finally:
            await bridge.close()

    asyncio.create_task(cleanup())

    await bridge.initialize()

    return bridge


@define
class HueLightToggleKey:
    """
    Stream Deck key for toggling a single Hue light.
    """

    key: int
    bridge: HueBridgeV2
    light_id: str

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)
    unsubscribes: list[Callable] = field(init=False, factory=list)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this key.
        """
        self.deck = deck
        self.unsubscribes = [
            self.bridge.events.subscribe(
                self.on_hue_connected,
                (EventType.CONNECTED, EventType.DISCONNECTED),
            ),
            self.bridge.events.subscribe(
                self.on_hue_disconnected, EventType.DISCONNECTED
            ),
            self.bridge.lights.subscribe(
                self.on_hue_light, id_filter=self.light_id
            ),
        ]
        self.set_tile_to_light(light_id=self.light_id)

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
        self.set_tile_to_light(light_id=self.light_id)

    async def on_hue_disconnected(
        self, _event_type: EventType, _event: dict | None = None
    ) -> None:
        """
        The Hue bridge was disconnected.
        """
        self.set_tile_to_light()

    async def on_key_change(self, key_state: bool) -> None:
        """
        The Stream Deck key was pressed or released.
        """
        # Ignore key release
        if not key_state:
            return

        try:
            light = self.bridge.lights[self.light_id]
            await self.bridge.lights.set_state(
                self.light_id, on=not light.on.on
            )
        except Exception:  # pylint: disable=W0718
            logging.error("Failed to set light state", exc_info=True)
            self.set_tile_to_light(light_id=self.light_id)

    def set_tile_to_light(
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
            if light_id:
                light = self.bridge.lights.get(self.light_id)

            if not light:
                text = "Disconnected"
                icon = "lightbulb-question-outline"
                color = "#330000"
            else:
                device = self.bridge.lights.get_device(self.light_id)
                text = device.metadata.name

                if light.is_on:
                    icon = "lightbulb-outline"
                    if light.supports_color:
                        x, y = light.color.xy.x, light.color.xy.y
                        brightness = light.brightness / 100.0
                        rgb = xyb_to_rgb(x, y, brightness)
                        scaled_rgb = scale_rgb_tuple(rgb, down=False)
                        color = rgb_to_hex(scaled_rgb)
                        logging.debug(f"  color: {rgb} {scaled_rgb} {color}")
                    else:
                        color = "#996633"
                else:
                    icon = "lightbulb-off-outline"
                    color = "#330000"
        except Exception:  # pylint: disable=W0718
            logging.error("Couldn't get light information", exc_info=True)
            text = "Error"
            icon = "lightbulb-alert-outline"
            color = "#330000"

        logging.debug(f"Setting key {self.key} to icon {icon}: {text!r}")
        tile = self.controller.draw_tile(text, color, icon)

        self.deck.set_key_image(self.key, tile)

    async def on_hue_light(self, _: EventType, light=None) -> None:
        """
        A Hue event was received.
        """
        if light.id != self.light_id:
            logging.debug(f"Ignoring light {light}")
            return

        self.set_tile_to_light(light)
