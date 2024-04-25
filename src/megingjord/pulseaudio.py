"""
PulseAudio utilities.
"""

import asyncio
import logging
import pprint
from contextlib import suppress
from typing import Any, AsyncIterator

from aiohttp import web
from attrs import define, field
from pulsectl import (
    PulseCardInfo,
    PulseCardPortInfo,
    PulseDisconnected,
    PulseEventInfo,
    PulseSinkInfo,
)
from pulsectl_asyncio import PulseAsync
from StreamDeck.Devices.StreamDeck import StreamDeck

from .streamdeck import DeckController

logger = logging.getLogger(__name__)

ICON_MAP = {
    None: "speaker-off",
    "audio-card-analog-usb": "usb",
    "audio-card-analog-pci": "music-box-outline",
    "audio-headphones-bluetooth": "headphones-bluetooth",
    "audio-headphones": "headphones",
    "audio-speakers": "speaker",
    "audio-speakers-bluetooth": "speaker-bluetooth",
    "video-display": "monitor",
}


@define
class PulseAudioCoordinator:
    """
    PulseAudio coordinator.

    This helper connects to PulseAudio, waiting for server events and yielding
    changes to the current default sink.
    """

    app: web.Application
    output_weights: list[dict[str, Any]]
    pulse: PulseAsync | None = field(init=False, default=None)
    subscribers: dict[object, asyncio.Queue[PulseEventInfo]] = field(
        init=False, factory=dict
    )

    def __attrs_post_init__(self) -> None:
        self.app.cleanup_ctx.append(self.start)

    async def get_pulse(self) -> PulseAsync:
        """
        Get PulseAudio instance and optionally connect.
        """
        if self.pulse:
            return self.pulse

        self.pulse = PulseAsync("megingjord")
        await self.pulse.connect()
        logger.info("Connected to PulseAudio")
        return self.pulse

    async def listen(self) -> None:
        """
        Subscribe for PulseAudio server events.
        """
        logger.info("Waiting for events")

        try:
            while True:
                pulse = await self.get_pulse()

                await pulse._connected.wait()  # pylint: disable=W0212

                logger.info("Subscribing to events")

                try:
                    async for event in pulse.subscribe_events(
                        "server", "card"
                    ):
                        logger.debug(f"Yielding {event!r}")
                        for queue in self.subscribers.values():
                            await queue.put(event)
                except PulseDisconnected:
                    logger.info("Disconnected from PulseAudio")
                    self.pulse = None
                except Exception:  # pylint: disable=W0718
                    logger.error(
                        "Exception raised while processing events",
                        exc_info=True,
                    )
        finally:
            logger.debug("Cleaning up subscibers")
            for queue in self.subscribers.values():
                await queue.put(None)

    async def start(self, _app: web.Application) -> AsyncIterator[None]:
        """
        Start the coordinator.

        This initiates listening for Pulse Audio events and cleans up when
        the task is cancelled upon shutdown.
        """

        task = asyncio.create_task(self.listen())

        yield

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def get_default_sink(self) -> PulseSinkInfo:
        """
        Extract default sink from server info.
        """
        pulse = await self.get_pulse()
        await pulse._connected.wait()  # pylint: disable=W0212
        info = await pulse.server_info()
        return info.default_sink_name

    async def listen_default_sink(self) -> AsyncIterator[PulseSinkInfo]:
        """
        Listen for default sink changes.
        """
        key = object()
        queue: asyncio.Queue[PulseEventInfo] = asyncio.Queue()
        self.subscribers[key] = queue

        done = False

        while not done:
            logger.debug("Not done yet")
            yield await self.get_default_sink()
            done = await queue.get() is None

    async def get_sink_for_port(
        self, card: PulseCardInfo, port: PulseCardPortInfo
    ) -> PulseSinkInfo:
        """
        Find an available sink for the requested port in the current profile.
        """
        pulse = await self.get_pulse()

        sinks = await pulse.sink_list()
        for sink in sinks:
            if (
                sink.card == card.index
                and sink.port_active
                and sink.port_active.name == port.name
            ):
                return sink

        return None

    async def set_default_sink(
        self, card: PulseCardInfo, port: PulseCardPortInfo
    ) -> None:
        """
        Set default sink.
        """
        pulse = await self.get_pulse()

        port_sink = await self.get_sink_for_port(card, port)

        if not port_sink:
            # We probably need to switch profiles
            original_profile = card.profile_active
            for profile_name in sorted(
                card.profile_list, key=lambda profile: profile.priority
            ):
                if profile_name == original_profile:
                    continue  # Already tried above

                await pulse.card_profile_set(card, profile_name)
                port_sink = await self.get_sink_for_port(card, port)

                if port_sink:
                    break

        await pulse.default_set(port_sink)

    async def get_card_port_from_sink(
        self, sink: PulseSinkInfo
    ) -> tuple[PulseCardInfo, PulseCardPortInfo]:
        """
        Get the Pulse Audio card and port for a given sink.
        """
        pulse = await self.get_pulse()
        card = await pulse.card_info(sink.card)
        for port in card.port_list:
            if port.name == sink.port_active.name:
                return card, port

        raise KeyError("Port not found")

    async def get_outputs(self) -> list[dict[str, Any]]:
        """
        Get all available outputs, ordered by priority.
        """
        pulse = await self.get_pulse()
        cards = await pulse.card_list()

        def check_resource(
            matcher: dict[str, Any],
            resource: PulseCardInfo | PulseCardPortInfo,
        ) -> bool:
            if "name" in matcher:
                if resource.name != matcher["name"]:
                    return False
            for key, value in matcher.get("proplist", {}).items():
                if key not in resource.proplist:
                    return False
                if value != resource.proplist[key]:
                    return False
            return True

        def check_rule(rule: dict[str, Any], card: PulseCardInfo, port:
                       PulseCardPortInfo) -> bool:
            return check_resource(
                rule.get("card", {}), card
            ) and check_resource(rule.get("port", {}), port)

        outputs: list[dict[str, Any]] = []
        for card in cards:
            for port in card.port_list:
                if port.direction == "output" and port.available != "no":
                    weight_adjust = 0
                    for weight_rule in self.output_weights:
                        if check_rule(weight_rule, card, port):
                            weight_adjust += weight_rule["weight"]
                    outputs.append(
                        {
                            "card": card,
                            "port": port,
                            "priority": port.priority + weight_adjust,
                        }
                    )

        return sorted(outputs, key=lambda item: item["priority"], reverse=True)

    async def get_next_available_output(self) -> dict[str, Any] | None:
        """
        Get next available output.
        """

        pulse = await self.get_pulse()
        server_info = await pulse.server_info()
        current_sink_name = server_info.default_sink_name
        current_sink = await pulse.get_sink_by_name(current_sink_name)

        try:
            outputs = await self.get_outputs()
            logger.debug(f"Possible outputs: {pprint.pformat(outputs)}")
        except Exception:  # pylint: disable=W0718
            logger.error("oops", exc_info=True)
            raise

        logger.debug(f"  Current Sink: {current_sink}")
        logger.debug("  Trying outputs:")

        # Switch to first available different output
        for output in outputs:
            logger.debug(f"    Output {output}:")
            if (
                output["card"].index == current_sink.card
                and output["port"].name == current_sink.port_active.name
            ):
                logger.debug(
                    "      This is the currently active output, skipping"
                )
                # Skip current sink
                continue

            return output

        return None


@define
class PulseDefaultSinkKey:
    """
    Stream Deck key for switching the default PulseAudio sink.
    """

    key: int
    pulse: PulseAudioCoordinator

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)
    current_sink_name: str | None = field(init=False, default=None)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this key.
        """
        self.deck = deck
        async for sink_name in self.pulse.listen_default_sink():
            await self.on_sink(sink_name)

    async def stop(self) -> None:
        """
        Stop this key.
        """
        self.deck.set_key_image(self.key, None)

    async def on_key_change(self, key_state: bool) -> None:
        """
        The Stream Deck key was pressed or released.
        """
        # Ignore key release
        if not key_state:
            return

        logger.debug("Key pressed:")

        output = await self.pulse.get_next_available_output()
        if not output:
            logger.info("      No output to change to.")
            return

        logger.debug("      Setting as default")
        try:
            await self.pulse.set_default_sink(output["card"], output["port"])
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to set default sink", exc_info=True)

    async def on_sink(self, sink_name: str) -> None:
        """
        The PulseAudio default sink changed.
        """
        if not self.pulse.pulse or not self.controller:
            return

        try:
            self.current_sink_name = sink_name
            new_sink = await self.pulse.pulse.get_sink_by_name(sink_name)
            card, port = await self.pulse.get_card_port_from_sink(new_sink)

            primary_icon = self.get_port_icon(card, port)

            output = await self.pulse.get_next_available_output()
            if output:
                secondary_icon = self.get_port_icon(
                    output["card"], output["port"]
                )
            else:
                secondary_icon = None
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to retrieve output details", exc_info=True)
            primary_icon = "help-rhombus-outline"
            secondary_icon = None

        tile = await self.controller.draw_tile(
            self.get_device_name(card, port),
            "#336699",
            primary_icon,
            secondary_icon,
            subtitle=self.get_port_name(port),
        )
        self.deck.set_key_image(self.key, tile)

    def get_port_icon(
        self, card: PulseCardInfo, port: PulseCardPortInfo
    ) -> str | None:
        """
        Get the icon for a Pulse Audio port on a card.
        """
        return ICON_MAP.get(
            port.proplist.get(
                "device.icon_name", card.proplist.get("device.icon_name")
            )
        )

    def get_port_name(self, port: PulseCardPortInfo) -> str:
        """
        Get the name for a Pulse Audio port.
        """
        return str(port.description)

    def get_device_name(
        self, card: PulseCardInfo, port: PulseCardPortInfo
    ) -> str:
        """
        Get the name for a Pulse Audio port.
        """
        return str(port.proplist.get(
            "device.product.name",
            card.proplist.get("device.description", "Output"),
        ))
