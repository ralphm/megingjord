# SPDX-License-Identifier: MIT

"""
Tests for L{megingjord.streamdeck}.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from PIL import Image

from megingjord.color_utils import get_colors
from megingjord.icon import get_icon, svg_icon
from megingjord.render import Renderer, wrap_text
from megingjord.streamdeck import DeckController, DialEventType

pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web.NotAppKeyWarning")


def make_renderer() -> Renderer:
    """
    A renderer with a default theme.
    """
    app = web.Application()
    app["colors"] = get_colors("default")
    return Renderer(app)


def make_controller() -> DeckController:
    """
    A controller with a mocked deck.
    """
    app = web.Application()
    app["colors"] = get_colors("default")
    controller = DeckController(app)
    controller.deck = MagicMock()
    controller.deck.touchscreen_image_format.return_value = {
        "format": "JPEG",
        "size": (800, 100),
        "rotation": 0,
        "flip": (False, False),
    }
    return controller


class TestGetColor:
    """
    Tests for L{megingjord.render.Renderer.get_color}.
    """

    def test_override_hex(self) -> None:
        """
        A hex override is returned as-is.
        """
        renderer = make_renderer()
        assert (
            renderer.get_color(
                "icon-primary", overrides={"icon-primary": "#990000"}
            )
            == "#990000"
        )

    def test_override_rgba(self) -> None:
        """
        An rgba override is returned as-is.
        """
        renderer = make_renderer()
        assert (
            renderer.get_color(
                "icon-primary",
                overrides={"icon-primary": "rgba(153, 0, 0, 0.50)"},
            )
            == "rgba(153, 0, 0, 0.50)"
        )

    def test_override_theme_name(self) -> None:
        """
        A theme color name override is resolved.
        """
        renderer = make_renderer()
        assert (
            renderer.get_color(
                "icon-primary", overrides={"icon-primary": "icon-alert"}
            )
            == "#990000"
        )


class TestWrapText:
    """
    Tests for L{megingjord.render.wrap_text}.
    """

    def test_short(self) -> None:
        """
        A short title stays on one line.
        """
        assert wrap_text("Office", 12, 2) == ["Office"]

    def test_wraps_to_two_lines(self) -> None:
        """
        A title that wraps to two lines keeps both.
        """
        lines = wrap_text("Kantoor Ralph", 12, 2)
        assert lines == ["Kantoor", "Ralph"]

    def test_truncates_overflow(self) -> None:
        """
        A title longer than max_lines is truncated with an ellipsis.
        """
        lines = wrap_text("Kantoor Ralph Plafond", 12, 2)
        assert len(lines) <= 2
        assert lines[1].endswith("…")


class TestSvgIcon:
    """
    Tests for L{megingjord.icon.svg_icon} and L{megingjord.icon.get_icon}.
    """

    @pytest.mark.asyncio
    async def test_get_icon_cached(self) -> None:
        """
        The parsed icon SVG is cached per icon and size.
        """
        svg1 = await get_icon("shield", 80)
        svg2 = await get_icon("shield", 80)
        assert svg1 is svg2

    @pytest.mark.asyncio
    async def test_svg_icon_colors_do_not_share(self) -> None:
        """
        Icons with different colors do not share the cached SVG.
        """
        svg1 = await svg_icon("shield", "red", 80)
        svg2 = await svg_icon("shield", "blue", 80)
        assert svg1 is not svg2
        fill1 = str(next(iter(svg1)).fill)
        fill2 = str(next(iter(svg2)).fill)
        assert fill1 != fill2


class TestRenderBlocks:
    """
    Tests for L{megingjord.render.Renderer} blocks.
    """

    @pytest.mark.asyncio
    async def test_state_tile(self) -> None:
        """
        A state tile renders as a JPEG image.
        """
        renderer = make_renderer()
        tile = await renderer.draw_state_tile(
            "Office", icon="lightbulb", subtitle="On"
        )
        assert tile[:2] == b"\xff\xd8"

    @pytest.mark.asyncio
    async def test_state_tile_text_only(self) -> None:
        """
        A state tile without an icon renders.
        """
        renderer = make_renderer()
        tile = await renderer.draw_state_tile("Office")
        assert tile[:2] == b"\xff\xd8"

    @pytest.mark.asyncio
    async def test_transition_tile(self) -> None:
        """
        A transition tile renders as a JPEG image.
        """
        renderer = make_renderer()
        tile = await renderer.draw_transition_tile(
            "Office", primary_icon="shield-off", secondary_icon="shield-moon"
        )
        assert tile[:2] == b"\xff\xd8"

    @pytest.mark.asyncio
    async def test_transition_tile_badge(self) -> None:
        """
        A transition tile with a badge renders.
        """
        renderer = make_renderer()
        tile = await renderer.draw_transition_tile(
            "Office",
            primary_icon="shield-off",
            secondary_icon="shield-moon",
            badge="alert-circle",
        )
        assert tile[:2] == b"\xff\xd8"

    @pytest.mark.asyncio
    async def test_state_dial_mini(self) -> None:
        """
        The state dial mini variant renders.
        """
        renderer = make_renderer()
        state = await renderer.draw_state_dial(
            "Home Alarm", "Pending", "shield-outline", mini=True
        )
        assert state.size == (140, 100)

    @pytest.mark.asyncio
    async def test_selection_dial_multi(self) -> None:
        """
        The selection dial renders prev/next items and a subtitle.
        """
        renderer = make_renderer()
        prev_item = MagicMock()
        prev_item.icon = "shield"
        prev_item.current = False
        prev_item.color = None
        item = MagicMock()
        item.icon = "shield-moon"
        item.title = "Armed home"
        item.subtitle = "Target"
        item.current = True
        item.color = None
        next_item = MagicMock()
        next_item.icon = "shield-off"
        next_item.current = False
        next_item.color = None
        view = MagicMock()
        view.items = [prev_item, item, next_item]
        view.selected = 1
        image = await renderer.draw_selection_dial(view)
        assert image.size == (140, 100)

    @pytest.mark.asyncio
    async def test_selection_dial_mini(self) -> None:
        """
        The selection dial mini variant renders.
        """
        renderer = make_renderer()
        item = MagicMock()
        item.icon = "shield"
        item.title = "Disarmed"
        item.subtitle = None
        item.current = True
        item.color = None
        view = MagicMock()
        view.items = [item]
        view.selected = 0
        image = await renderer.draw_selection_dial(view, mini=True)
        assert image.size == (140, 100)

    @pytest.mark.asyncio
    async def test_value_dial_mini(self) -> None:
        """
        The value dial mini variant and an empty meter render.
        """
        renderer = make_renderer()
        value = await renderer.draw_value_dial(
            "Office", "lightbulb", 0, mini=True
        )
        assert value.size == (140, 100)

    @pytest.mark.asyncio
    async def test_dial_blocks(self) -> None:
        """
        The dial blocks render 140x100 LCD images.
        """
        renderer = make_renderer()
        state = await renderer.draw_state_dial(
            "Home Alarm", "Pending", "shield-outline"
        )
        assert state.size == (140, 100)

        item = MagicMock()
        item.icon = "shield"
        item.title = "Disarmed"
        item.subtitle = None
        item.current = True
        item.color = None
        view = MagicMock()
        view.items = [item]
        view.selected = 0
        selection = await renderer.draw_selection_dial(view)
        assert selection.size == (140, 100)

        value = await renderer.draw_value_dial("Office", "lightbulb", 0.5)
        assert value.size == (140, 100)

    @pytest.mark.asyncio
    async def test_draw_time(self) -> None:
        """
        The status bar time renders.
        """
        renderer = make_renderer()
        image = renderer.draw_time()
        assert image.size == (440, 50)

    @pytest.mark.asyncio
    async def test_value_dial_long_title(self) -> None:
        """
        A long title does not overflow the top of the dial tile.
        """
        renderer = make_renderer()
        image = await renderer.draw_value_dial(
            "Kantoor Ralph Plafond", "lightbulb", 0.5
        )
        px = image.load()
        top_rows = [
            any(px[x, y][3] > 0 for x in range(image.width)) for y in range(3)
        ]
        assert not any(top_rows)


class FakeDial:
    """
    A dial with mocked handlers.
    """

    def __init__(self) -> None:
        self.on_dial_push = AsyncMock()
        self.on_dial_turn = AsyncMock()

    async def render(self, mini: bool = False) -> Image.Image:
        """
        Render a tile.
        """
        return Image.new("RGBA", (220, 100))


class FakeKey:
    """
    A key with a mocked controller.
    """

    def __init__(self, key: int) -> None:
        self.key = key
        self.controller = None


class TestKeyRegistration:
    """
    Tests for L{megingjord.streamdeck.DeckController} key registration.
    """

    def test_register(self) -> None:
        """
        Registering a key adds it and sets the controller.
        """
        controller = make_controller()
        key = FakeKey(0)
        controller.register_key(key)
        assert controller.keys[0] is key
        assert key.controller is controller

    def test_unregister(self) -> None:
        """
        Unregistering a key removes it and clears the controller.
        """
        controller = make_controller()
        key = FakeKey(0)
        controller.register_key(key)
        controller.unregister_key(key)
        assert 0 not in controller.keys
        assert key.controller is None

    def test_unregister_twice(self) -> None:
        """
        Unregistering a key twice does not raise.
        """
        controller = make_controller()
        key = FakeKey(0)
        controller.register_key(key)
        controller.unregister_key(key)
        controller.unregister_key(key)
        assert 0 not in controller.keys


class TestOnDialChange:
    """
    Tests for L{megingjord.streamdeck.DeckController.on_dial_change}.
    """

    @pytest.mark.asyncio
    async def test_turn_inhibits(self) -> None:
        """
        Turning dial 1 inhibits the status bar and dispatches the turn.
        """
        controller = make_controller()
        dial = FakeDial()
        controller.dials[1] = dial
        await controller.on_dial_change(
            controller.deck, 1, DialEventType.TURN, 5
        )
        assert controller.status_inhibited > 0
        dial.on_dial_turn.assert_awaited_once_with(5)
        controller.deck.set_touchscreen_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_inhibits(self) -> None:
        """
        Pushing dial 2 inhibits the status bar and dispatches the push.
        """
        controller = make_controller()
        dial = FakeDial()
        controller.dials[2] = dial
        await controller.on_dial_change(
            controller.deck, 2, DialEventType.PUSH, True
        )
        assert controller.status_inhibited > 0
        dial.on_dial_push.assert_awaited_once_with(True)
        controller.deck.set_touchscreen_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_inhibits(self) -> None:
        """
        Releasing a dial is part of the interaction and inhibits too.
        """
        controller = make_controller()
        dial = FakeDial()
        controller.dials[1] = dial
        await controller.on_dial_change(
            controller.deck, 1, DialEventType.PUSH, False
        )
        assert controller.status_inhibited > 0
        dial.on_dial_push.assert_awaited_once_with(False)
        controller.deck.set_touchscreen_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_other_dials_do_not_inhibit(self) -> None:
        """
        Turning dials 0 and 3 does not inhibit the status bar.
        """
        controller = make_controller()
        dial = FakeDial()
        controller.dials[0] = dial
        controller.dials[3] = dial
        await controller.on_dial_change(
            controller.deck, 0, DialEventType.TURN, 5
        )
        await controller.on_dial_change(
            controller.deck, 3, DialEventType.TURN, 5
        )
        assert controller.status_inhibited == 0
        assert dial.on_dial_turn.await_count == 2
        assert controller.deck.set_touchscreen_image.call_count == 2

    @pytest.mark.asyncio
    async def test_unknown_dial_ignored(self) -> None:
        """
        Events for unregistered dials are ignored.
        """
        controller = make_controller()
        dial = FakeDial()
        controller.dials[1] = dial
        await controller.on_dial_change(
            controller.deck, 2, DialEventType.TURN, 5
        )
        assert controller.status_inhibited == 0
        dial.on_dial_turn.assert_not_called()
        controller.deck.set_touchscreen_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_error_logged(self) -> None:
        """
        A failing dial handler is logged, not raised.
        """
        controller = make_controller()
        dial = FakeDial()
        dial.on_dial_turn = AsyncMock(side_effect=Exception("boom"))
        controller.dials[1] = dial
        await controller.on_dial_change(
            controller.deck, 1, DialEventType.TURN, 5
        )
        controller.deck.set_touchscreen_image.assert_called_once()


class TestKeyAnimation:
    """
    Tests for L{megingjord.streamdeck.DeckController} key animations.
    """

    @pytest.mark.asyncio
    async def test_animation_renders(self) -> None:
        """
        The animation renders frames and sets the key image.
        """
        controller = make_controller()
        phases: list[float] = []

        async def render(phase: float) -> bytes:
            phases.append(phase)
            return b"tile"

        controller.start_key_animation(0, render)
        await asyncio.sleep(0.25)
        controller.stop_key_animation(0)
        assert len(phases) >= 2
        assert controller.deck.set_key_image.call_count >= 2

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        """
        Starting an animation twice does not duplicate it.
        """
        controller = make_controller()

        async def render(phase: float) -> bytes:
            return b"tile"

        controller.start_key_animation(0, render)
        controller.start_key_animation(0, render)
        assert len(controller.animations) == 1
        controller.stop_key_animation(0)
        assert 0 not in controller.animations

    @pytest.mark.asyncio
    async def test_stop_cancels(self) -> None:
        """
        Stopping an animation cancels its task.
        """
        controller = make_controller()

        async def render(phase: float) -> bytes:
            return b"tile"

        controller.start_key_animation(0, render)
        task = controller.animations[0]
        controller.stop_key_animation(0)
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_stop_clears_all(self) -> None:
        """
        Stopping the controller cancels all animations.
        """
        controller = make_controller()

        async def render(phase: float) -> bytes:
            return b"tile"

        controller.start_key_animation(0, render)
        controller.start_key_animation(1, render)
        await controller.stop()
        assert controller.animations == {}
