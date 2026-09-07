# SPDX-License-Identifier: MIT

"""
PulseAudio utilities.
"""

from __future__ import annotations

import asyncio
import logging
import pprint
from contextlib import suppress
from typing import Any, AsyncIterator

from aiohttp import web
from attrs import define, field
from PIL import Image
from pulsectl import (
    PulseCardInfo,
    PulseCardPortInfo,
    PulseDisconnected,
    PulseEventInfo,
    PulseSinkInfo,
    PulseSourceInfo,
)
from pulsectl_asyncio import PulseAsync
from StreamDeck.Devices.StreamDeck import StreamDeck

from .streamdeck import DeckController, ScrollerItem, ScrollerView

logger = logging.getLogger(__name__)

ICON_MAP = {
    None: "speaker-off",
    "audio-card-analog-usb": "usb",
    "audio-card-analog-pci": "music-box-outline",
    "audio-headphones-bluetooth": "headphones-bluetooth",
    "audio-headphones": "headphones",
    "audio-headset-bluetooth": "headphones-bluetooth",
    "audio-speakers": "speaker",
    "audio-speakers-bluetooth": "speaker-bluetooth",
    "video-display": "monitor",
    "camera-web-analog-usb": "webcam",
    "audio-input-microphone": "microphone",
}


def get_port_icon(card: PulseCardInfo, port: PulseCardPortInfo) -> str:
    """
    Get the icon for a Pulse Audio port on a card.
    """
    icon = port.proplist.get(
        "device.icon_name", card.proplist.get("device.icon_name")
    )
    if icon not in ICON_MAP:
        logger.debug(f"  port icon: {port.proplist.get('device.icon_name')}")
        logger.debug(f"  card icon: {card.proplist.get('device.icon_name')}")

    return ICON_MAP.get(
        port.proplist.get(
            "device.icon_name", card.proplist.get("device.icon_name")
        ),
        ICON_MAP[None],
    )


def get_port_name(port: PulseCardPortInfo) -> str:
    """
    Get the name for a Pulse Audio port.
    """
    return str(port.description)


def get_device_name(card: PulseCardInfo, port: PulseCardPortInfo) -> str:
    """
    Get the name for a Pulse Audio port.
    """
    return str(
        port.proplist.get(
            "device.product.name",
            card.proplist.get("device.description", "Output"),
        )
    )


@define
class PulseOutput:
    """
    PulseAudio Output.

    A PulseAudio output is a combination of a card and port. Additional meta
    data include its priority based on the port priority, adjusted by
    configuration.
    """

    coordinator: PulseAudioCoordinator = field(repr=False)
    card: PulseCardInfo
    port: PulseCardPortInfo
    active: bool = False

    @property
    def priority(self) -> int:
        """
        Output priority.
        """

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

        def check_rule(rule: dict[str, Any]) -> bool:
            return check_resource(
                rule.get("card", {}), self.card
            ) and check_resource(rule.get("port", {}), self.port)

        weight_adjust = 0
        for weight_rule in self.coordinator.output_weights:
            if check_rule(weight_rule):
                weight_adjust += weight_rule["weight"]

        return int(self.port.priority) + weight_adjust

    @property
    def icon(self) -> str:
        """
        Get the port icon.
        """
        return get_port_icon(self.card, self.port)

    @property
    def port_name(self) -> str:
        """
        Get the port name.
        """
        return get_port_name(self.port)

    @property
    def device_name(self) -> str:
        """
        Get the device name.
        """
        return get_device_name(self.card, self.port)

    def matches_sink(self, sink: PulseSinkInfo) -> bool:
        """
        This output matches the given sink.
        """
        return bool(
            sink.card == self.card.index
            and sink.port_active
            and sink.port_active.name == self.port.name
        )

    async def get_sink(self) -> PulseSinkInfo:
        """
        Find an available sink for the requested port in the current profile.
        """
        pulse = await self.coordinator.get_pulse()
        sinks = await pulse.sink_list()
        for sink in sinks:
            if self.matches_sink(sink):
                return sink

        return None


@define
class PulseOutputScrollerItem(ScrollerItem):
    """
    L{ScrollerItem} wrapper for L{PulseOutput}.
    """

    wrapped: PulseOutput
    color: str | None = None

    @property
    def title(self) -> str:
        return self.wrapped.device_name

    @property
    def subtitle(self) -> str | None:
        return self.wrapped.port_name

    @property
    def icon(self) -> str:
        return self.wrapped.icon

    @property
    def current(self) -> bool:
        return self.wrapped.active


@define
class PulseInput:
    """
    PulseAudio Input.

    A PulseAudio input is a combination of a card and port. Additional meta
    data include its priority based on the port priority, adjusted by
    configuration.
    """

    coordinator: PulseAudioCoordinator = field(repr=False)
    card: PulseCardInfo
    port: PulseCardPortInfo
    active: bool = False

    @property
    def priority(self) -> int:
        """
        Input priority.
        """

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

        def check_rule(rule: dict[str, Any]) -> bool:
            return check_resource(
                rule.get("card", {}), self.card
            ) and check_resource(rule.get("port", {}), self.port)

        weight_adjust = 0
        for weight_rule in self.coordinator.input_weights:
            if check_rule(weight_rule):
                weight_adjust += weight_rule["weight"]

        return int(self.port.priority) + weight_adjust

    @property
    def icon(self) -> str:
        """
        Get the port icon.
        """
        return get_port_icon(self.card, self.port)

    @property
    def port_name(self) -> str:
        """
        Get the port name.
        """
        return get_port_name(self.port)

    @property
    def device_name(self) -> str:
        """
        Get the device name.
        """
        return get_device_name(self.card, self.port)

    def matches_source(self, source: PulseSourceInfo) -> bool:
        """
        This source matches the given source.
        """
        return bool(
            source.card == self.card.index
            and source.port_active
            and source.port_active.name == self.port.name
        )

    async def get_source(self) -> PulseSourceInfo:
        """
        Find an available source for the requested port in the current profile.
        """
        pulse = await self.coordinator.get_pulse()
        sources = await pulse.source_list()
        for source in sources:
            if self.matches_source(source):
                return source

        return None


@define
class PulseInputScrollerItem(ScrollerItem):
    """
    L{ScrollerItem} wrapper for L{PulseInput}.
    """

    wrapped: PulseInput
    color: str | None = None

    @property
    def title(self) -> str:
        return self.wrapped.device_name

    @property
    def subtitle(self) -> str | None:
        return self.wrapped.port_name

    @property
    def icon(self) -> str:
        return self.wrapped.icon

    @property
    def current(self) -> bool:
        return self.wrapped.active


@define
class PulseAudioCoordinator:
    """
    PulseAudio coordinator.

    This helper connects to PulseAudio, waiting for server events and yielding
    changes to the current default sink.
    """

    # pylint: disable=R0902

    app: web.Application
    output_weights: list[dict[str, Any]]
    input_weights: list[dict[str, Any]]
    pulse: PulseAsync | None = field(init=False, default=None)
    subscribers: dict[object, asyncio.Queue[PulseEventInfo]] = field(
        init=False, factory=dict
    )

    outputs: dict[tuple[int, str], PulseOutput] = field(
        init=False, factory=dict
    )
    inputs: dict[tuple[int, str], PulseInput] = field(init=False, factory=dict)

    default_sink_name: str | None = field(init=False, default=None)
    default_output: PulseOutput | None = field(init=False, default=None)

    default_source_name: str | None = field(init=False, default=None)
    default_input: PulseInput | None = field(init=False, default=None)

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

        if info.default_sink_name != self.default_sink_name:
            if self.default_output:
                self.default_output.active = False

            sink = await pulse.get_sink_by_name(info.default_sink_name)
            output = await self.get_output_from_sink(sink)
            output.active = True
            self.default_output = output

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

    async def set_default_sink(self, output: PulseOutput) -> None:
        """
        Set default sink.
        """
        pulse = await self.get_pulse()

        port_sink = await output.get_sink()

        if not port_sink:
            # We probably need to switch profiles
            original_profile = output.card.profile_active
            for profile_name in sorted(
                output.card.profile_list, key=lambda profile: profile.priority
            ):
                if profile_name == original_profile:
                    continue  # Already tried above

                await pulse.card_profile_set(output.card, profile_name)
                port_sink = await output.get_sink()

                if port_sink:
                    break

        await pulse.default_set(port_sink)

    def get_output_from_card_port(
        self, card: PulseCardInfo, port: PulseCardPortInfo
    ) -> PulseOutput:
        """
        Get the PulseAudio output for the given card and port.
        """
        output = self.outputs.get((card.index, port.name))
        if output is None:
            output = PulseOutput(self, card=card, port=port)
            self.outputs[card.index, port.name] = output
        return output

    async def get_output_from_sink(self, sink: PulseSinkInfo) -> PulseOutput:
        """
        Get the Pulse Audio card and port for a given sink.
        """
        pulse = await self.get_pulse()
        card = await pulse.card_info(sink.card)
        for port in card.port_list:
            if port.name == sink.port_active.name:
                return self.get_output_from_card_port(card, port)

        raise KeyError("Port not found")

    async def get_outputs(self) -> list[PulseOutput]:
        """
        Get all available outputs, ordered by priority.
        """
        pulse = await self.get_pulse()
        cards = await pulse.card_list()

        outputs: list[PulseOutput] = []
        for card in cards:
            for port in card.port_list:
                if port.direction == "output" and port.available != "no":
                    outputs.append(self.get_output_from_card_port(card, port))

        return sorted(outputs, key=lambda item: item.priority, reverse=True)

    async def get_next_available_output(self) -> PulseOutput | None:
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
            if output.matches_sink(current_sink):
                logger.debug(
                    "      This is the currently active output, skipping"
                )
                # Skip current sink
                continue

            return output

        return None

    async def get_default_source(self) -> PulseSourceInfo:
        """
        Extract default source from server info.
        """
        pulse = await self.get_pulse()
        await pulse._connected.wait()  # pylint: disable=W0212
        info = await pulse.server_info()

        if info.default_source_name != self.default_source_name:
            if self.default_input:
                self.default_input.active = False

            source = await pulse.get_source_by_name(info.default_source_name)
            source_obj = await self.get_input_from_source(source)
            source_obj.active = True
            self.default_input = source_obj

        return info.default_source_name

    async def listen_default_source(self) -> AsyncIterator[PulseSourceInfo]:
        """
        Listen for default source changes.
        """
        key = object()
        queue: asyncio.Queue[PulseEventInfo] = asyncio.Queue()
        self.subscribers[key] = queue

        done = False

        while not done:
            logger.debug("Not done yet")
            yield await self.get_default_source()
            done = await queue.get() is None

    async def set_default_source(self, source: PulseInput) -> None:
        """
        Set default source.
        """
        pulse = await self.get_pulse()

        port_source = await source.get_source()

        if not port_source:
            # We probably need to switch profiles
            original_profile = source.card.profile_active
            for profile_name in sorted(
                source.card.profile_list, key=lambda profile: profile.priority
            ):
                if profile_name == original_profile:
                    continue  # Already tried above

                await pulse.card_profile_set(source.card, profile_name)
                port_source = await source.get_source()

                if port_source:
                    break

        await pulse.default_set(port_source)

    def get_input_from_card_port(
        self, card: PulseCardInfo, port: PulseCardPortInfo
    ) -> PulseInput:
        """
        Get the PulseAudio input for the given card and port.
        """
        # pylint: disable-next=W0622
        input = self.inputs.get((card.index, port.name))
        if input is None:
            input = PulseInput(self, card=card, port=port)
            self.inputs[card.index, port.name] = input
        return input

    async def get_input_from_source(
        self, source: PulseSourceInfo
    ) -> PulseInput:
        """
        Get the Pulse Audio card and port for a given source.
        """
        pulse = await self.get_pulse()
        card = await pulse.card_info(source.card)
        for port in card.port_list:
            if port.name == source.port_active.name:
                return self.get_input_from_card_port(card, port)

        raise KeyError("Port not found")

    async def get_inputs(self) -> list[PulseInput]:
        """
        Get all available inputs, ordered by priority.
        """
        pulse = await self.get_pulse()
        cards = await pulse.card_list()

        sources: list[PulseInput] = []
        for card in cards:
            for port in card.port_list:
                if port.direction == "input" and port.available != "no":
                    sources.append(self.get_input_from_card_port(card, port))

        return sorted(sources, key=lambda item: item.priority, reverse=True)

    async def get_next_available_input(self) -> PulseInput | None:
        """
        Get next available input.
        """
        pulse = await self.get_pulse()
        server_info = await pulse.server_info()
        current_source_name = server_info.default_source_name
        current_source = await pulse.get_source_by_name(current_source_name)

        try:
            sources = await self.get_inputs()
            logger.debug(f"Possible inputs: {pprint.pformat(sources)}")
        except Exception:  # pylint: disable=W0718
            logger.error("oops", exc_info=True)
            raise

        logger.debug(f"  Current Source: {current_source}")
        logger.debug("  Trying inputs:")

        # Switch to first available different input
        for source in sources:
            logger.debug(f"    Input {source}:")
            if source.matches_source(current_source):
                logger.debug(
                    "      This is the currently active input, skipping"
                )
                # Skip current input
                continue

            return source

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
            await self.pulse.set_default_sink(output)
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to set default sink", exc_info=True)

    async def on_sink(self, sink_name: str) -> None:
        """
        The PulseAudio default sink changed.
        """
        if not self.pulse.pulse or not self.controller:
            return

        title = "Unknown"
        subtitle = None
        output: PulseOutput | None

        try:
            self.current_sink_name = sink_name
            new_sink = await self.pulse.pulse.get_sink_by_name(sink_name)
            output = await self.pulse.get_output_from_sink(new_sink)

            title = output.device_name
            subtitle = output.port_name
            primary_icon = output.icon

            output = await self.pulse.get_next_available_output()
            if output:
                secondary_icon = output.icon
            else:
                secondary_icon = None

        except Exception:  # pylint: disable=W0718
            logger.error("Failed to retrieve output details", exc_info=True)
            primary_icon = "help-rhombus-outline"
            secondary_icon = None

        tile = await self.controller.renderer.draw_transition_tile(
            title=title,
            primary_icon=primary_icon,
            secondary_icon=secondary_icon,
            subtitle=subtitle,
        )
        self.deck.set_key_image(self.key, tile)


@define
class PulseDefaultSinkDial:
    """
    Stream Deck dial control for switching the default PulseAudio sink.
    """

    dial: int
    pulse: PulseAudioCoordinator
    current_view: ScrollerView | None = field(init=False, default=None)
    current_sink_name: str | None = field(init=False, default=None)

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start the dial.
        """
        self.deck = deck
        async for sink_name in self.pulse.listen_default_sink():
            await self.on_sink(sink_name)

    async def stop(self) -> None:
        """
        Stop the dial.
        """

        if self.controller is not None:
            await self.controller.render_lcd()

    async def on_sink(self, sink_name: str) -> None:
        """
        The PulseAudio default sink changed.
        """
        if not self.pulse.pulse or not self.controller:
            return

        self.current_sink_name = sink_name

        self.current_view = await self.scroller_view_from_default_output()

        await self.controller.render_lcd()

    async def on_dial_push(self, dial_state: bool) -> None:
        """
        Called when the dial got pressed or released.
        """
        if (
            not self.pulse.pulse
            or not self.controller
            or not self.current_view
        ):
            return

        output = self.current_view.selected_item.wrapped

        assert isinstance(output, PulseOutput)

        if dial_state:
            await self.pulse.set_default_sink(output)

    async def on_dial_turn(self, value: int) -> None:
        """
        Called when the dial got turned.
        """
        if (
            not self.pulse.pulse
            or not self.controller
            or not self.current_sink_name
        ):
            return

        if not self.current_view:
            return None

        self.current_view.turn(value)

    async def scroller_view_from_default_output(self) -> ScrollerView | None:
        """
        Get a scroller view.
        """

        if (
            not self.pulse.pulse
            or not self.controller
            or not self.pulse.default_output
        ):
            return None

        items = []
        selected = 0
        index = 0
        for output in await self.pulse.get_outputs():
            item = PulseOutputScrollerItem(output)
            if output == self.pulse.default_output:
                selected = index
            items.append(item)
            index += 1

        return ScrollerView(items=items, selected=selected)

    async def render(self, mini: bool = False) -> Image.Image:
        """
        Render the portial of the LCD display (tile) for this dial.
        """
        view = self.current_view

        if not self.pulse.pulse or not self.controller or view is None:
            return Image.new("RGBA", (140, 100), "#00000000")

        image = await self.controller.renderer.draw_selection_dial(
            view=view,
            mini=mini,
        )

        return image


@define
class PulseDefaultSourceKey:
    """
    Stream Deck key for switching the default PulseAudio source.
    """

    key: int
    pulse: PulseAudioCoordinator

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)
    current_source_name: str | None = field(init=False, default=None)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start this key.
        """
        self.deck = deck
        async for source_name in self.pulse.listen_default_source():
            await self.on_source(source_name)

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

        source = await self.pulse.get_next_available_input()
        if not source:
            logger.info("      No source to change to.")
            return

        logger.debug("      Setting as default")
        try:
            await self.pulse.set_default_source(source)
        except Exception:  # pylint: disable=W0718
            logger.error("Failed to set default source", exc_info=True)

    async def on_source(self, source_name: str) -> None:
        """
        The PulseAudio default source changed.
        """
        if not self.pulse.pulse or not self.controller:
            return

        title = "Unknown"
        subtitle = None
        source: PulseInput | None

        try:
            self.current_source_name = source_name
            new_source = await self.pulse.pulse.get_source_by_name(source_name)
            source = await self.pulse.get_input_from_source(new_source)

            title = source.device_name
            subtitle = source.port_name
            primary_icon = source.icon

            source = await self.pulse.get_next_available_input()
            if source:
                secondary_icon = source.icon
            else:
                secondary_icon = None

        except Exception:  # pylint: disable=W0718
            logger.error("Failed to retrieve source details", exc_info=True)
            primary_icon = "help-rhombus-outline"
            secondary_icon = None

        tile = await self.controller.renderer.draw_transition_tile(
            title=title,
            primary_icon=primary_icon,
            secondary_icon=secondary_icon,
            subtitle=subtitle,
        )
        self.deck.set_key_image(self.key, tile)


@define
class PulseDefaultSourceDial:
    """
    Stream Deck dial control for switching the default PulseAudio source.
    """

    dial: int
    pulse: PulseAudioCoordinator
    current_view: ScrollerView | None = field(init=False, default=None)
    current_source_name: str | None = field(init=False, default=None)

    controller: DeckController | None = field(init=False)
    deck: StreamDeck = field(init=False)

    async def start(self, deck: StreamDeck) -> None:
        """
        Start the dial.
        """
        self.deck = deck
        async for source_name in self.pulse.listen_default_source():
            await self.on_source(source_name)

    async def stop(self) -> None:
        """
        Stop the dial.
        """
        if self.controller is not None:
            await self.controller.render_lcd()

    async def on_source(self, source_name: str) -> None:
        """
        The PulseAudio default source changed.
        """
        if not self.pulse.pulse or not self.controller:
            return

        self.current_source_name = source_name

        self.current_view = await self.scroller_view_from_default_source()

        await self.controller.render_lcd()

    async def on_dial_push(self, dial_state: bool) -> None:
        """
        Called when the dial got pressed or released.
        """
        if (
            not self.pulse.pulse
            or not self.controller
            or not self.current_view
        ):
            return

        source = self.current_view.selected_item.wrapped

        assert isinstance(source, PulseInput)

        if dial_state:
            await self.pulse.set_default_source(source)

    async def on_dial_turn(self, value: int) -> None:
        """
        Called when the dial got turned.
        """
        if (
            not self.pulse.pulse
            or not self.controller
            or not self.current_source_name
        ):
            return

        if not self.current_view:
            return None

        self.current_view.turn(value)

    async def scroller_view_from_default_source(self) -> ScrollerView | None:
        """
        Get a scroller view.
        """
        if (
            not self.pulse.pulse
            or not self.controller
            or not self.pulse.default_input
        ):
            return None

        items = []
        selected = 0
        index = 0
        for source in await self.pulse.get_inputs():
            item = PulseInputScrollerItem(source)
            if source == self.pulse.default_input:
                selected = index
            items.append(item)
            index += 1

        return ScrollerView(items=items, selected=selected)

    async def render(self, mini: bool = False) -> Image.Image:
        """
        Render the portion of the LCD display (tile) for this dial.
        """
        view = self.current_view

        if not self.pulse.pulse or not self.controller or view is None:
            return Image.new("RGBA", (140, 100), "#00000000")

        image = await self.controller.renderer.draw_selection_dial(
            view=view,
            mini=mini,
        )

        return image
