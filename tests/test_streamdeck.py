# SPDX-License-Identifier: MIT

"""
Tests for L{megingjord.streamdeck}.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from megingjord.color_utils import get_colors
from megingjord.icon import get_icon
from megingjord.streamdeck import DeckController, svg_icon

pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web.NotAppKeyWarning")


def make_controller() -> DeckController:
    """
    A controller with a mocked deck.
    """
    app = web.Application()
    app["colors"] = get_colors("default")
    controller = DeckController(app)
    controller.deck = MagicMock()
    return controller


class TestGetColor:
    """
    Tests for L{megingjord.streamdeck.DeckController.get_color}.
    """

    def test_override_hex(self) -> None:
        """
        A hex override is returned as-is.
        """
        controller = make_controller()
        assert (
            controller.get_color(
                "icon-primary", overrides={"icon-primary": "#990000"}
            )
            == "#990000"
        )

    def test_override_rgba(self) -> None:
        """
        An rgba override is returned as-is.
        """
        controller = make_controller()
        assert (
            controller.get_color(
                "icon-primary",
                overrides={"icon-primary": "rgba(153, 0, 0, 0.50)"},
            )
            == "rgba(153, 0, 0, 0.50)"
        )

    def test_override_theme_name(self) -> None:
        """
        A theme color name override is resolved.
        """
        controller = make_controller()
        assert (
            controller.get_color(
                "icon-primary", overrides={"icon-primary": "icon-alert"}
            )
            == "#990000"
        )


class TestSvgIcon:
    """
    Tests for L{megingjord.streamdeck.svg_icon} and
    L{megingjord.icon.get_icon}.
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
