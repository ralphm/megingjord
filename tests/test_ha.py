"""
Tests for L{megingjord.ha}.
"""

import asyncio
import logging
import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from PIL import Image

from megingjord.ha import (
    SEND_DELAY_SECONDS,
    AlarmModeScrollerItem,
    HAAlarmDial,
    HAAlarmTile,
    HAEntityDial,
    HAEntityTile,
    HAWebSocketClient,
    get_entity_icon,
    get_state_text,
    icon_from_range,
    icon_from_translations,
    normalize_url,
)


class TestNormalizeUrl:
    """
    Tests for L{megingjord.ha.normalize_url}.
    """

    def test_ws_url(self) -> None:
        """
        A WebSocket URL is returned unchanged.
        """
        url = "ws://ha.local:8123/api/websocket"
        assert normalize_url(url) == url

    def test_wss_url(self) -> None:
        """
        A secure WebSocket URL is returned unchanged.
        """
        url = "wss://ha.local:8123/api/websocket"
        assert normalize_url(url) == url

    def test_http_url(self) -> None:
        """
        An HTTP URL is converted to a WebSocket URL.
        """
        assert (
            normalize_url("http://ha.local:8123")
            == "ws://ha.local:8123/api/websocket"
        )

    def test_https_url(self) -> None:
        """
        An HTTPS URL is converted to a secure WebSocket URL.
        """
        assert (
            normalize_url("https://ha.local:8123")
            == "wss://ha.local:8123/api/websocket"
        )

    def test_http_url_with_path(self) -> None:
        """
        An HTTP URL with the WebSocket path is converted correctly.
        """
        assert (
            normalize_url("http://ha.local:8123/api/websocket")
            == "ws://ha.local:8123/api/websocket"
        )

    def test_invalid_url(self) -> None:
        """
        An invalid URL raises a ValueError.
        """
        try:
            normalize_url("ha.local:8123")
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError")


class TestGetEntityIcon:
    """
    Tests for L{megingjord.ha.get_entity_icon}.
    """

    def test_override(self) -> None:
        """
        An icon override wins.
        """
        assert get_entity_icon("light.test", {}, "custom") == "custom"

    def test_entity_icon(self) -> None:
        """
        An entity icon attribute is used.
        """
        assert get_entity_icon("light.test", {"icon": "mdi:lamp"}) == "lamp"

    def test_non_mdi_icon_ignored(self) -> None:
        """
        A non-MDI entity icon is ignored.
        """
        assert (
            get_entity_icon("light.test", {"icon": "http://x"}) == "lightbulb"
        )

    def test_domain_default(self) -> None:
        """
        The domain default icon is used.
        """
        assert get_entity_icon("media_player.tv", {}) == "speaker"

    def test_unknown_domain(self) -> None:
        """
        An unknown domain falls back to a generic icon.
        """
        assert get_entity_icon("foo.bar", {}) == "toggle-switch"


@pytest.fixture
def ha_client() -> tuple[HAWebSocketClient, MagicMock]:
    """
    A client with a mocked hass_client.
    """
    with patch("megingjord.ha.HomeAssistantClient") as mock_class:
        client = HAWebSocketClient(
            web.Application(), "ws://ha.local/api/websocket", "token"
        )
        client.client = mock_class.return_value
        yield client, mock_class


class TestHAWebSocketClient:
    """
    Tests for L{megingjord.ha.HAWebSocketClient}.
    """

    def test_init(self) -> None:
        """
        The client registers a cleanup context without creating a client.
        """
        with patch("megingjord.ha.HomeAssistantClient") as mock_class:
            client = HAWebSocketClient(
                web.Application(), "ws://ha.local/api/websocket", "token"
            )
            mock_class.assert_not_called()
            assert client.client is None
            assert client.connected is False
            assert client.get_state("light.test") is None
            assert client.start in client.app.cleanup_ctx

    def test_subscribe(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Subscribing registers a callback, unsubscribing removes it.
        """

        async def callback(state: dict | None) -> None:
            pass

        client, _ = ha_client
        unsubscribe = client.subscribe("light.test", callback)
        assert callback in client.subscribers["light.test"]
        unsubscribe()
        assert callback not in client.subscribers["light.test"]

    @pytest.mark.asyncio
    async def test_subscribe_missing(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Subscribing to a non-existent entity marks it missing.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.subscribe_entities = AsyncMock()
        notified: list[dict | None] = []

        async def callback(state: dict | None) -> None:
            notified.append(state)

        client._connected = True
        client.subscribe("light.test", callback)
        await asyncio.sleep(0.05)
        assert client.is_missing("light.test")
        assert notified == [None]

    @pytest.mark.asyncio
    async def test_subscribe_missing_cleared(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A state arriving for a missing entity clears the flag.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.subscribe_entities = AsyncMock()

        async def callback(state: dict | None) -> None:
            pass

        client._connected = True
        client.subscribe("light.test", callback)
        await asyncio.sleep(0.05)
        assert client.is_missing("light.test")
        client._on_entity_event(
            {"a": {"light.test": {"s": "on", "a": {"brightness": 128}}}}
        )
        assert not client.is_missing("light.test")
        assert client.get_state("light.test")["state"] == "on"

    @pytest.mark.asyncio
    async def test_call_service(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A service call is passed to hass_client with target and data.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.call_service = AsyncMock()
        await client.call_service(
            "light",
            "turn_on",
            entity_id="light.test",
            data={"brightness": 100},
        )
        mock.call_service.assert_awaited_once_with(
            "light",
            "turn_on",
            target={"entity_id": "light.test"},
            service_data={"brightness": 100},
        )

    @pytest.mark.asyncio
    async def test_call_service_no_kwargs(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A service call without target or data passes no extra kwargs.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.call_service = AsyncMock()
        await client.call_service("homeassistant", "toggle")
        mock.call_service.assert_awaited_once_with("homeassistant", "toggle")

    @pytest.mark.asyncio
    async def test_call_service_not_connected(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A service call without a client raises an error.
        """
        client, _ = ha_client
        client.client = None
        with pytest.raises(RuntimeError):
            await client.call_service("homeassistant", "toggle")

    @pytest.mark.asyncio
    async def test_connect(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Connecting subscribes per entity and notifies subscribers.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.connect = AsyncMock()
        mock.start_listening = AsyncMock()

        async def subscribe_entities(cb, entity_ids):
            cb({"a": {"light.test": {"s": "off", "a": {}}}})
            return lambda: None

        mock.subscribe_entities = AsyncMock(side_effect=subscribe_entities)

        done = asyncio.Event()
        states: list[dict | None] = []

        async def on_state(state: dict | None) -> None:
            states.append(state)
            if len(states) == 2:
                done.set()

        client.subscribe("light.test", on_state)

        await client._connect()

        mock.connect.assert_awaited_once()
        mock.subscribe_entities.assert_awaited_once()
        assert mock.subscribe_entities.call_args.args[1] == ["light.test"]
        assert client.connected is False
        assert client.get_state("light.test") is None
        await asyncio.wait_for(done.wait(), 1)
        assert states == [
            {
                "entity_id": "light.test",
                "state": "off",
                "attributes": {},
                "context": None,
                "last_changed": None,
                "last_updated": None,
            },
            None,
        ]

    @pytest.mark.asyncio
    async def test_connect_connected_state(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        While listening, the client is connected and states are available.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.connect = AsyncMock()

        async def subscribe_entities(cb, entity_ids):
            cb({"a": {"light.test": {"s": "off", "a": {}}}})
            return lambda: None

        mock.subscribe_entities = AsyncMock(side_effect=subscribe_entities)

        listening = asyncio.Event()
        release = asyncio.Event()

        async def start_listening() -> None:
            listening.set()
            await release.wait()

        mock.start_listening = start_listening

        async def on_state(state: dict | None) -> None:
            pass

        client.subscribe("light.test", on_state)

        task = asyncio.create_task(client._connect())
        await asyncio.wait_for(listening.wait(), 1)
        assert client.connected is True
        assert client.get_state("light.test") == {
            "entity_id": "light.test",
            "state": "off",
            "attributes": {},
            "context": None,
            "last_changed": None,
            "last_updated": None,
        }
        release.set()
        await task
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_on_entity_event_added(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        An added entity state is expanded and notifies subscribers.
        """
        client, _ = ha_client
        notified = asyncio.Event()
        states: list[dict | None] = []

        async def on_state(state: dict | None) -> None:
            states.append(state)
            notified.set()

        client.subscribe("light.test", on_state)
        client._on_entity_event(
            {"a": {"light.test": {"s": "on", "a": {"brightness": 128}}}}
        )
        await asyncio.wait_for(notified.wait(), 1)
        state = client.get_state("light.test")
        assert state is not None
        assert state["state"] == "on"
        assert state["attributes"] == {"brightness": 128}
        assert states == [state]

    @pytest.mark.asyncio
    async def test_on_entity_event_changed(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A state change diff is merged into the cached state.
        """
        client, _ = ha_client
        client.states["light.test"] = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"brightness": 128},
        }
        client._on_entity_event({"c": {"light.test": {"+": {"s": "off"}}}})
        state = client.get_state("light.test")
        assert state is not None
        assert state["state"] == "off"
        assert state["attributes"] == {"brightness": 128}

    @pytest.mark.asyncio
    async def test_on_entity_event_attributes(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Attribute additions and removals are applied.
        """
        client, _ = ha_client
        client.states["light.test"] = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"brightness": 128},
        }
        client._on_entity_event(
            {
                "c": {
                    "light.test": {
                        "+": {"a": {"color_temp": 300}},
                        "-": {"a": ["brightness"]},
                    }
                }
            }
        )
        state = client.get_state("light.test")
        assert state is not None
        assert state["attributes"] == {"color_temp": 300}

    @pytest.mark.asyncio
    async def test_on_entity_event_removed(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A removed entity clears the state and notifies subscribers.
        """
        client, _ = ha_client
        client.states["light.test"] = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {},
        }
        notified = asyncio.Event()
        states: list[dict | None] = []

        async def on_state(state: dict | None) -> None:
            states.append(state)
            notified.set()

        client.subscribe("light.test", on_state)
        client._on_entity_event({"r": ["light.test"]})
        await asyncio.wait_for(notified.wait(), 1)
        assert client.get_state("light.test") is None
        assert states == [None]

    def test_on_entity_event_other_entity(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Events for other entities are ignored.
        """
        client, _ = ha_client
        called: list[dict | None] = []
        client.subscribe("light.other", lambda state: called.append(state))
        client._on_entity_event({"a": {"light.test": {"s": "on", "a": {}}}})
        assert called == []
        assert client.get_state("light.test") is not None

    def test_expand_state(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A compact state is expanded to the full format.
        """
        client, _ = ha_client
        state = client._expand_state(
            "light.test",
            {"s": "on", "a": {"brightness": 128}, "c": "ctx-1", "lc": 1000},
        )
        assert state == {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"brightness": 128},
            "context": {"id": "ctx-1", "parent_id": None, "user_id": None},
            "last_changed": "1970-01-01T00:16:40+00:00",
            "last_updated": "1970-01-01T00:16:40+00:00",
        }

    @pytest.mark.asyncio
    async def test_connect_error_cancels_listener(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A failed command cancels the listener and cleans up.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.connect = AsyncMock()
        mock.subscribe_entities = AsyncMock(side_effect=Exception("boom"))

        async def start_listening() -> None:
            await asyncio.Event().wait()

        mock.start_listening = start_listening

        client.subscribe("light.test", lambda state: None)
        with pytest.raises(Exception):
            await client._connect()
        assert client.connected is False
        assert client.get_state("light.test") is None

    @pytest.mark.asyncio
    async def test_run_reconnects(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        The run loop retries after a failed connection.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.connect = AsyncMock(side_effect=[Exception("boom"), None])
        mock.subscribe_entities = AsyncMock()
        mock.start_listening = AsyncMock()
        client.retry_delay = 0.01

        task = asyncio.create_task(client.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert mock.connect.await_count >= 2

    @pytest.mark.asyncio
    async def test_start_stop(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        The cleanup context creates the client, starts and stops it.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.connect = AsyncMock()
        mock.subscribe_entities = AsyncMock()
        mock.start_listening = AsyncMock()
        mock.disconnect = AsyncMock()
        client.retry_delay = 0.01

        gen = client.start(web.Application())
        await gen.__anext__()
        mock_class.assert_called_once_with(
            "ws://ha.local/api/websocket", "token"
        )
        assert client.client is mock
        assert client.task is not None
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        mock.disconnect.assert_awaited_once()


@pytest.fixture
def tile() -> HAEntityTile:
    """
    A tile with mocked HA client, controller and deck.
    """
    ha = MagicMock()
    ha.subscribe.return_value = lambda: None
    ha.get_state.return_value = None
    ha.is_missing.return_value = False
    ha.get_entity_icon = AsyncMock(
        side_effect=lambda entity_id, attributes, icon=None, state=None: (
            get_entity_icon(entity_id, attributes, icon)
        )
    )
    tile = HAEntityTile(0, ha, "light.test")
    tile.controller = AsyncMock()
    tile.controller.draw_tile.return_value = b"tile"
    tile.deck = MagicMock()
    return tile


class TestHAEntityTile:
    """
    Tests for L{megingjord.ha.HAEntityTile}.
    """

    @pytest.mark.asyncio
    async def test_start(self, tile: HAEntityTile) -> None:
        """
        Starting subscribes and draws the tile.
        """
        await tile.start(tile.deck)
        tile.ha.subscribe.assert_called_once_with("light.test", tile.on_state)
        tile.deck.set_key_image.assert_called_once_with(0, b"tile")

    @pytest.mark.asyncio
    async def test_stop(self, tile: HAEntityTile) -> None:
        """
        Stopping unsubscribes and clears the key image.
        """
        unsubscribed: list[bool] = []
        tile.ha.subscribe.return_value = lambda: unsubscribed.append(True)
        await tile.start(tile.deck)
        await tile.stop()
        assert unsubscribed == [True]
        tile.deck.set_key_image.assert_called_with(0, None)

    @pytest.mark.asyncio
    async def test_on_key_change(self, tile: HAEntityTile) -> None:
        """
        Pressing the key toggles the entity.
        """
        tile.ha.call_service = AsyncMock()
        await tile.on_key_change(True)
        tile.ha.call_service.assert_awaited_once_with(
            "homeassistant", "toggle", entity_id="light.test"
        )

    @pytest.mark.asyncio
    async def test_on_key_change_release(self, tile: HAEntityTile) -> None:
        """
        Releasing the key does nothing.
        """
        tile.ha.call_service = AsyncMock()
        await tile.on_key_change(False)
        tile.ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_key_change_error(self, tile: HAEntityTile) -> None:
        """
        A failed service call redraws the tile.
        """
        tile.ha.call_service = AsyncMock(side_effect=Exception("boom"))
        await tile.on_key_change(True)
        tile.controller.draw_tile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_state(self, tile: HAEntityTile) -> None:
        """
        A state change redraws the tile.
        """
        await tile.on_state(None)
        tile.controller.draw_tile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_tile_disconnected(self, tile: HAEntityTile) -> None:
        """
        Without a state, the tile shows disconnected.
        """
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Disconnected",
            {"icon-primary": "icon-inactive", "tile-bg": "tile-inactive-bg"},
            "cloud-question-outline",
            subtitle=None,
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_missing(self, tile: HAEntityTile) -> None:
        """
        A missing entity shows entity not found.
        """
        tile.ha.is_missing.return_value = True
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Entity not found",
            {"icon-primary": "icon-inactive", "tile-bg": "tile-inactive-bg"},
            "alert-outline",
            subtitle=None,
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_on(self, tile: HAEntityTile) -> None:
        """
        An on state uses the active icon color.
        """
        tile.ha.get_state.return_value = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"friendly_name": "Test Light"},
        }
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Test Light",
            {"icon-primary": "icon-active"},
            "lightbulb",
            subtitle="On",
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_on_rgb(self, tile: HAEntityTile) -> None:
        """
        An on state with an RGB color uses that color.
        """
        tile.ha.get_state.return_value = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {
                "friendly_name": "Test Light",
                "rgb_color": [255, 0, 0],
            },
        }
        await tile.set_tile()
        colors = tile.controller.draw_tile.await_args.args[1]
        assert colors["icon-primary"] == "#FF0000"
        assert tile.controller.draw_tile.await_args.kwargs["subtitle"] == "On"

    @pytest.mark.asyncio
    async def test_set_tile_off(self, tile: HAEntityTile) -> None:
        """
        An off state uses the off icon and inactive colors.
        """
        tile.ha.get_state.return_value = {
            "entity_id": "light.test",
            "state": "off",
            "attributes": {"friendly_name": "Test Light"},
        }
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Test Light",
            {"icon-primary": "icon-inactive", "tile-bg": "tile-inactive-bg"},
            "lightbulb-off-outline",
            subtitle="Off",
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_unavailable(self, tile: HAEntityTile) -> None:
        """
        An unavailable state uses the alert color.
        """
        tile.ha.get_state.return_value = {
            "entity_id": "light.test",
            "state": "unavailable",
            "attributes": {"friendly_name": "Test Light"},
        }
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Test Light",
            {"icon-primary": "icon-inactive", "tile-bg": "tile-inactive-bg"},
            "lightbulb",
            subtitle="Unavailable",
            badge="alert-circle",
        )

    @pytest.mark.asyncio
    async def test_set_tile_no_controller(self, tile: HAEntityTile) -> None:
        """
        Without a controller, nothing is drawn.
        """
        tile.controller = None
        await tile.set_tile()
        tile.deck.set_key_image.assert_not_called()


def make_dial(entity_id: str, state: dict | None) -> HAEntityDial:
    """
    A dial with mocked HA client, controller and deck.
    """
    ha = MagicMock()
    ha.subscribe.return_value = lambda: None
    ha.get_state.return_value = state
    ha.is_missing.return_value = False
    ha.get_entity_icon = AsyncMock(
        side_effect=lambda entity_id, attributes, icon=None, state=None: (
            get_entity_icon(entity_id, attributes, icon)
        )
    )
    dial = HAEntityDial(0, ha, entity_id)
    dial.controller = AsyncMock()
    dial.controller.draw_dial_tile.return_value = Image.new("RGBA", (140, 100))
    dial.deck = MagicMock()
    return dial


@pytest.fixture
def dial() -> HAEntityDial:
    """
    A number dial with no state.
    """
    return make_dial("number.test", None)


class TestHAEntityDial:
    """
    Tests for L{megingjord.ha.HAEntityDial}.
    """

    @pytest.mark.asyncio
    async def test_start(self, dial: HAEntityDial) -> None:
        """
        Starting subscribes and renders the dial.
        """
        await dial.start(dial.deck)
        dial.ha.subscribe.assert_called_once_with("number.test", dial.on_state)
        dial.controller.draw_dial_tile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_with_state(self, dial: HAEntityDial) -> None:
        """
        Starting with a state initializes the value.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        await dial.start(dial.deck)
        assert dial.value == 0.5
        dial.controller.draw_dial_tile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop(self, dial: HAEntityDial) -> None:
        """
        Stopping unsubscribes and renders the LCD.
        """
        unsubscribed: list[bool] = []
        dial.ha.subscribe.return_value = lambda: unsubscribed.append(True)
        await dial.start(dial.deck)
        await dial.stop()
        assert unsubscribed == [True]
        dial.controller.render_lcd.assert_awaited_once_with(tile_changed=0)

    @pytest.mark.asyncio
    async def test_on_state(self, dial: HAEntityDial) -> None:
        """
        A state change updates the value and renders the LCD.
        """
        state = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        await dial.on_state(state)
        assert dial.value == 0.5
        dial.controller.render_lcd.assert_awaited_once_with(tile_changed=0)

    @pytest.mark.asyncio
    async def test_on_state_pending(self, dial: HAEntityDial) -> None:
        """
        A state change does not update the value while pending.
        """
        dial.pending = 1
        state = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        await dial.on_state(state)
        assert dial.value == 0.0
        dial.controller.render_lcd.assert_awaited_once_with(tile_changed=0)

    @pytest.mark.asyncio
    async def test_render_disconnected(self, dial: HAEntityDial) -> None:
        """
        Without a state, the dial shows disconnected.
        """
        await dial.render()
        dial.controller.draw_dial_tile.assert_awaited_once_with(
            title="Disconnected",
            icon="cloud-question-outline",
            value=0.0,
            mini=False,
        )

    @pytest.mark.asyncio
    async def test_render_missing(self, dial: HAEntityDial) -> None:
        """
        A missing entity shows entity not found.
        """
        dial.ha.is_missing.return_value = True
        await dial.render()
        dial.controller.draw_dial_tile.assert_awaited_once_with(
            title="Entity not found",
            icon="alert-outline",
            value=0.0,
            mini=False,
        )

    @pytest.mark.asyncio
    async def test_render_number(self, dial: HAEntityDial) -> None:
        """
        A number entity renders its value as a fraction.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"friendly_name": "Volume", "min": 0, "max": 100},
        }
        dial.value = 0.5
        await dial.render()
        dial.controller.draw_dial_tile.assert_awaited_once_with(
            title="Volume", icon="numeric", value=0.5, mini=False
        )

    @pytest.mark.asyncio
    async def test_render_light(self) -> None:
        """
        A light entity renders its brightness as a fraction.
        """
        dial = make_dial(
            "light.test",
            {
                "entity_id": "light.test",
                "state": "on",
                "attributes": {"friendly_name": "Lamp", "brightness": 128},
            },
        )
        dial.value = 128 / 255
        await dial.render()
        dial.controller.draw_dial_tile.assert_awaited_once_with(
            title="Lamp", icon="lightbulb", value=128 / 255, mini=False
        )

    @pytest.mark.asyncio
    async def test_render_media_player(self) -> None:
        """
        A media player renders its volume level.
        """
        dial = make_dial(
            "media_player.tv",
            {
                "entity_id": "media_player.tv",
                "state": "playing",
                "attributes": {
                    "friendly_name": "TV",
                    "volume_level": 0.7,
                },
            },
        )
        dial.value = 0.7
        await dial.render()
        dial.controller.draw_dial_tile.assert_awaited_once_with(
            title="TV", icon="speaker", value=0.7, mini=False
        )

    @pytest.mark.asyncio
    async def test_render_no_deck(self, dial: HAEntityDial) -> None:
        """
        Without a deck, an empty image is returned.
        """
        dial.deck = None
        image = await dial.render()
        assert isinstance(image, Image.Image)
        dial.controller.draw_dial_tile.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_dial_turn(self, dial: HAEntityDial) -> None:
        """
        Turning the dial updates the value, renders and sends the command.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        dial.ha.call_service = AsyncMock()
        await dial.on_dial_turn(1)
        assert dial.value == pytest.approx(0.01)
        dial.controller.render_lcd.assert_awaited_once_with(tile_changed=0)
        await asyncio.sleep(SEND_DELAY_SECONDS + 0.05)
        dial.ha.call_service.assert_awaited_once()
        value = dial.ha.call_service.await_args.kwargs["data"]["value"]
        assert value == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_on_dial_turn_coalesces(self, dial: HAEntityDial) -> None:
        """
        Rapid turns coalesce into a single command.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        dial.ha.call_service = AsyncMock()
        await dial.on_dial_turn(1)
        await dial.on_dial_turn(1)
        await dial.on_dial_turn(1)
        assert dial.value == pytest.approx(0.03)
        await asyncio.sleep(SEND_DELAY_SECONDS + 0.05)
        dial.ha.call_service.assert_awaited_once()
        value = dial.ha.call_service.await_args.kwargs["data"]["value"]
        assert value == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_on_dial_turn_disconnected(self, dial: HAEntityDial) -> None:
        """
        Turning the dial without a state updates locally but sends nothing.
        """
        dial.ha.call_service = AsyncMock()
        await dial.on_dial_turn(1)
        assert dial.value == pytest.approx(0.01)
        dial.controller.render_lcd.assert_awaited_once_with(tile_changed=0)
        await asyncio.sleep(SEND_DELAY_SECONDS + 0.05)
        dial.ha.call_service.assert_not_called()

    def test_get_value_number(self, dial: HAEntityDial) -> None:
        """
        A number entity value is a fraction of its range.
        """
        state = {
            "entity_id": "number.test",
            "state": "25",
            "attributes": {"min": 0, "max": 100},
        }
        assert dial._get_value(state) == 0.25

    def test_get_value_number_invalid(self, dial: HAEntityDial) -> None:
        """
        An invalid number state yields zero.
        """
        state = {
            "entity_id": "number.test",
            "state": "abc",
            "attributes": {"min": 0, "max": 100},
        }
        assert dial._get_value(state) == 0.0

    def test_get_value_number_bad_range(self, dial: HAEntityDial) -> None:
        """
        A number entity with an inverted range yields zero.
        """
        state = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 100, "max": 0},
        }
        assert dial._get_value(state) == 0.0

    def test_get_value_light(self) -> None:
        """
        A light entity value is its brightness fraction.
        """
        dial = make_dial("light.test", None)
        state = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"brightness": 128},
        }
        assert dial._get_value(state) == pytest.approx(128 / 255)

    def test_get_value_light_off(self) -> None:
        """
        An off light without brightness yields zero.
        """
        dial = make_dial("light.test", None)
        state = {
            "entity_id": "light.test",
            "state": "off",
            "attributes": {"brightness": None},
        }
        assert dial._get_value(state) == 0.0

    def test_get_value_media_player(self) -> None:
        """
        A media player value is its volume level.
        """
        dial = make_dial("media_player.tv", None)
        state = {
            "entity_id": "media_player.tv",
            "state": "playing",
            "attributes": {"volume_level": 0.7},
        }
        assert dial._get_value(state) == 0.7

    def test_get_value_media_player_none(self) -> None:
        """
        A media player without volume yields zero.
        """
        dial = make_dial("media_player.tv", None)
        state = {
            "entity_id": "media_player.tv",
            "state": "playing",
            "attributes": {"volume_level": None},
        }
        assert dial._get_value(state) == 0.0

    def test_get_value_unknown(self) -> None:
        """
        An unsupported domain yields zero.
        """
        dial = make_dial("switch.test", None)
        state = {"entity_id": "switch.test", "state": "on", "attributes": {}}
        assert dial._get_value(state) == 0.0

    @pytest.mark.asyncio
    async def test_set_value_number(self, dial: HAEntityDial) -> None:
        """
        Setting a number entity value calls number.set_value.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        dial.ha.call_service = AsyncMock()
        await dial._set_value(0.5)
        dial.ha.call_service.assert_awaited_once_with(
            "number",
            "set_value",
            entity_id="number.test",
            data={"value": 50.0},
        )

    @pytest.mark.asyncio
    async def test_set_value_light_off(self) -> None:
        """
        Setting a light to zero turns it off.
        """
        dial = make_dial(
            "light.test",
            {"entity_id": "light.test", "state": "on", "attributes": {}},
        )
        dial.ha.call_service = AsyncMock()
        await dial._set_value(0.0)
        dial.ha.call_service.assert_awaited_once_with(
            "light", "turn_off", entity_id="light.test"
        )

    @pytest.mark.asyncio
    async def test_set_value_light_on(self) -> None:
        """
        Setting a light to a fraction turns it on with brightness.
        """
        dial = make_dial(
            "light.test",
            {"entity_id": "light.test", "state": "on", "attributes": {}},
        )
        dial.ha.call_service = AsyncMock()
        await dial._set_value(0.5)
        dial.ha.call_service.assert_awaited_once_with(
            "light",
            "turn_on",
            entity_id="light.test",
            data={"brightness": 128},
        )

    @pytest.mark.asyncio
    async def test_set_value_media_player(self) -> None:
        """
        Setting a media player volume calls volume_set.
        """
        dial = make_dial(
            "media_player.tv",
            {
                "entity_id": "media_player.tv",
                "state": "playing",
                "attributes": {},
            },
        )
        dial.ha.call_service = AsyncMock()
        await dial._set_value(0.3)
        dial.ha.call_service.assert_awaited_once_with(
            "media_player",
            "volume_set",
            entity_id="media_player.tv",
            data={"volume_level": 0.3},
        )

    @pytest.mark.asyncio
    async def test_set_value_disconnected(self, dial: HAEntityDial) -> None:
        """
        Setting a value without a state does nothing.
        """
        dial.ha.call_service = AsyncMock()
        await dial._set_value(0.5)
        dial.ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_value_unknown(self) -> None:
        """
        Setting a value for an unsupported domain does nothing.
        """
        dial = make_dial(
            "switch.test",
            {"entity_id": "switch.test", "state": "on", "attributes": {}},
        )
        dial.ha.call_service = AsyncMock()
        await dial._set_value(0.5)
        dial.ha.call_service.assert_not_called()

    def test_on_entity_event_empty(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        An empty event is ignored.
        """
        client, _ = ha_client
        client._on_entity_event({})
        assert client.states == {}

    @pytest.mark.asyncio
    async def test_stop_no_controller(self, dial: HAEntityDial) -> None:
        """
        Stopping without a controller does not render the LCD.
        """
        dial.controller = None
        await dial.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_send(self, dial: HAEntityDial) -> None:
        """
        Stopping cancels a pending send.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        dial.ha.call_service = AsyncMock()
        await dial.start(dial.deck)
        await dial.on_dial_turn(1)
        await asyncio.sleep(0.01)
        await dial.stop()
        await asyncio.sleep(SEND_DELAY_SECONDS + 0.05)
        dial.ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_state_no_controller(self, dial: HAEntityDial) -> None:
        """
        A state change without a controller does not render the LCD.
        """
        dial.controller = None
        await dial.on_state(None)

    @pytest.mark.asyncio
    async def test_on_dial_turn_no_controller(
        self, dial: HAEntityDial
    ) -> None:
        """
        Turning the dial without a controller does nothing.
        """
        dial.controller = None
        await dial.on_dial_turn(1)
        dial.ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_dial_turn_error(self, dial: HAEntityDial) -> None:
        """
        A failed value set still renders the LCD.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        dial.ha.call_service = AsyncMock(side_effect=Exception("boom"))
        await dial.on_dial_turn(1)
        dial.controller.render_lcd.assert_awaited_once_with(tile_changed=0)
        await asyncio.sleep(SEND_DELAY_SECONDS + 0.05)
        while dial.pending > 0:
            await asyncio.sleep(0.01)


class TestGetEntityIconAsync:
    """
    Tests for L{megingjord.ha.HAWebSocketClient.get_entity_icon}.
    """

    @pytest.mark.asyncio
    async def test_override_wins(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        An explicit icon override wins without any lookups.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        assert await client.get_entity_icon("light.test", {}, icon="custom")
        mock.get_entity_registry_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_entity_icon_wins(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        The entity icon attribute wins without any lookups.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        icon = await client.get_entity_icon("light.test", {"icon": "mdi:lamp"})
        assert icon == "lamp"
        mock.get_entity_registry_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_translation_icon(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Translation-key icons resolve from the integration's icons.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.get_entity_registry_entry = AsyncMock(
            return_value={
                "entity_id": "light.test",
                "platform": "hue",
                "translation_key": "hue_grouped_light",
            }
        )
        mock.send_command = AsyncMock(
            return_value={
                "resources": {
                    "hue": {
                        "light": {
                            "hue_grouped_light": {
                                "default": "mdi:lightbulb-group",
                                "state": {"off": "mdi:lightbulb-group-off"},
                            }
                        }
                    }
                }
            }
        )
        assert (
            await client.get_entity_icon("light.test", {}) == "lightbulb-group"
        )
        assert (
            await client.get_entity_icon("light.test", {}, state="off")
            == "lightbulb-group-off"
        )
        mock.send_command.assert_awaited_once_with(
            "frontend/get_icons", category="entity", integration="hue"
        )

    @pytest.mark.asyncio
    async def test_translation_icon_cached(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Platform icons are fetched once per integration.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.get_entity_registry_entry = AsyncMock(
            side_effect=[
                {
                    "entity_id": "light.a",
                    "platform": "hue",
                    "translation_key": "hue_grouped_light",
                },
                {
                    "entity_id": "light.b",
                    "platform": "hue",
                    "translation_key": "hue_grouped_light",
                },
            ]
        )
        mock.send_command = AsyncMock(
            return_value={
                "resources": {
                    "hue": {
                        "light": {
                            "hue_grouped_light": {
                                "default": "mdi:lightbulb-group"
                            }
                        }
                    }
                }
            }
        )
        assert await client.get_entity_icon("light.a", {}) == "lightbulb-group"
        assert await client.get_entity_icon("light.b", {}) == "lightbulb-group"
        mock.send_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_translation_icon_range(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Numeric states resolve range-based icons.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.get_entity_registry_entry = AsyncMock(
            return_value={
                "entity_id": "sensor.test",
                "platform": "test",
                "translation_key": "battery",
            }
        )
        mock.send_command = AsyncMock(
            return_value={
                "resources": {
                    "test": {
                        "sensor": {
                            "battery": {
                                "default": "mdi:battery",
                                "range": {
                                    "20": "mdi:battery-low",
                                    "80": "mdi:battery-high",
                                },
                            }
                        }
                    }
                }
            }
        )
        assert (
            await client.get_entity_icon("sensor.test", {}, state="50")
            == "battery-low"
        )
        assert (
            await client.get_entity_icon("sensor.test", {}, state="90")
            == "battery-high"
        )
        assert (
            await client.get_entity_icon("sensor.test", {}, state="5")
            == "battery"
        )

    @pytest.mark.asyncio
    async def test_no_translation_key(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Without a translation key, the domain default is used.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.get_entity_registry_entry = AsyncMock(
            return_value={"entity_id": "light.test", "platform": "hue"}
        )
        assert await client.get_entity_icon("light.test", {}) == "lightbulb"
        mock.send_command.assert_called_once_with(
            "frontend/get_icons",
            category="entity_component",
            integration=["light"],
        )

    @pytest.mark.asyncio
    async def test_unknown_entity(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Without a registry entry, the domain default is used.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.get_entity_registry_entry = AsyncMock(return_value=None)
        assert await client.get_entity_icon("light.test", {}) == "lightbulb"

    @pytest.mark.asyncio
    async def test_icon_fetch_error(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A failed icon lookup falls back to the domain default.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.get_entity_registry_entry = AsyncMock(
            side_effect=Exception("boom")
        )
        assert await client.get_entity_icon("light.test", {}) == "lightbulb"

    @pytest.mark.asyncio
    async def test_non_mdi_icon(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Non-mdi icons are returned as-is.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.get_entity_registry_entry = AsyncMock(
            return_value={
                "entity_id": "light.test",
                "platform": "hue",
                "translation_key": "hue_grouped_light",
            }
        )
        mock.send_command = AsyncMock(
            return_value={
                "resources": {
                    "hue": {
                        "light": {
                            "hue_grouped_light": {
                                "default": "hass:lightbulb-group"
                            }
                        }
                    }
                }
            }
        )
        assert (
            await client.get_entity_icon("light.test", {})
            == "hass:lightbulb-group"
        )

    @pytest.mark.asyncio
    async def test_no_platform_icons(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Without platform icons, the domain default is used.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.get_entity_registry_entry = AsyncMock(
            return_value={
                "entity_id": "light.test",
                "platform": "hue",
                "translation_key": "hue_grouped_light",
            }
        )
        mock.send_command = AsyncMock(return_value={"resources": {}})
        assert await client.get_entity_icon("light.test", {}) == "lightbulb"

    @pytest.mark.asyncio
    async def test_component_icon(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Component icons resolve by device class and state.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.send_command = AsyncMock(
            return_value={
                "resources": {
                    "cover": {
                        "_": {
                            "default": "mdi:window-open",
                            "state": {"closed": "mdi:window-closed"},
                        },
                        "garage": {
                            "default": "mdi:garage-open",
                            "state": {"closed": "mdi:garage"},
                        },
                    }
                }
            }
        )
        assert (
            await client.get_entity_icon(
                "cover.test", {"device_class": "garage"}, state="closed"
            )
            == "garage"
        )
        assert (
            await client.get_entity_icon(
                "cover.test", {"device_class": "garage"}
            )
            == "garage-open"
        )
        assert (
            await client.get_entity_icon("cover.test", {}, state="closed")
            == "window-closed"
        )
        assert await client.get_entity_icon("cover.test", {}) == "window-open"
        mock.send_command.assert_awaited_once_with(
            "frontend/get_icons",
            category="entity_component",
            integration=["cover"],
        )

    @pytest.mark.asyncio
    async def test_component_icon_cached(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Component icons are fetched once per domain.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.send_command = AsyncMock(
            return_value={
                "resources": {"cover": {"_": {"default": "mdi:window-open"}}}
            }
        )
        assert await client.get_entity_icon("cover.a", {}) == "window-open"
        assert await client.get_entity_icon("cover.b", {}) == "window-open"
        mock.send_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_component_icon_error(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A failed component icon lookup falls back to the domain default.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.send_command = AsyncMock(side_effect=Exception("boom"))
        assert await client.get_entity_icon("cover.test", {}) == "window-open"

    @pytest.mark.asyncio
    async def test_no_client(self) -> None:
        """
        Without a client, the domain default is used.
        """
        client = HAWebSocketClient(
            web.Application(), "ws://ha.local/api/websocket", "token"
        )
        assert await client.get_entity_icon("light.test", {}) == "lightbulb"


class TestIconFromTranslations:
    """
    Tests for L{megingjord.ha.icon_from_translations}.
    """

    def test_no_translations(self) -> None:
        """
        Without translations, no icon is returned.
        """
        assert icon_from_translations("on", None) is None

    def test_non_numeric_range_state(self) -> None:
        """
        A non-numeric state with a range falls back to the default.
        """
        translations = {"default": "mdi:battery", "range": {"20": "mdi:x"}}
        assert icon_from_translations("abc", translations) == "mdi:battery"

    def test_range_no_numeric_keys(self) -> None:
        """
        A range without numeric keys yields no icon.
        """
        assert icon_from_range(50, {"low": "mdi:x"}) is None

    def test_range_below_threshold(self) -> None:
        """
        A value below the first threshold yields no icon.
        """
        assert icon_from_range(5, {"20": "mdi:x"}) is None


class TestGetStateText:
    """
    Tests for L{megingjord.ha.get_state_text}.
    """

    def test_light_on(self) -> None:
        """
        An on light shows On.
        """
        state = {"entity_id": "light.test", "state": "on", "attributes": {}}
        assert get_state_text(state) == "On"

    def test_light_on_brightness(self) -> None:
        """
        An on light with brightness shows the percentage.
        """
        state = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {"brightness": 128},
        }
        assert get_state_text(state) == "On · 50%"

    def test_light_off(self) -> None:
        """
        An off light shows Off.
        """
        state = {"entity_id": "light.test", "state": "off", "attributes": {}}
        assert get_state_text(state) == "Off"

    def test_switch(self) -> None:
        """
        A switch shows On or Off.
        """
        state = {"entity_id": "switch.test", "state": "on", "attributes": {}}
        assert get_state_text(state) == "On"

    def test_unavailable(self) -> None:
        """
        An unavailable entity shows Unavailable.
        """
        state = {
            "entity_id": "light.test",
            "state": "unavailable",
            "attributes": {},
        }
        assert get_state_text(state) == "Unavailable"

    def test_unknown_domain(self) -> None:
        """
        An unknown domain shows the raw state.
        """
        state = {"entity_id": "sensor.test", "state": "42", "attributes": {}}
        assert get_state_text(state) == "42"

    def test_non_string_state(self) -> None:
        """
        A non-string state yields an empty text.
        """
        state = {"entity_id": "sensor.test", "state": 42, "attributes": {}}
        assert get_state_text(state) == ""


class TestSubscribeWhileConnected:
    """
    Tests for subscribing while connected.
    """

    @pytest.mark.asyncio
    async def test_subscribe_while_connected(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Subscribing while connected subscribes on the server.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.subscribe_entities = AsyncMock()
        client._connected = True

        async def on_state(state: dict | None) -> None:
            pass

        client.subscribe("light.test", on_state)
        await asyncio.sleep(0.01)
        mock.subscribe_entities.assert_awaited_once()
        assert mock.subscribe_entities.call_args.args[1] == ["light.test"]


class TestMergeDiff:
    """
    Tests for L{megingjord.ha.HAWebSocketClient._merge_diff}.
    """

    def test_no_cache(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A diff without a cached state is expanded.
        """
        client, _ = ha_client
        state = client._merge_diff("light.test", {"+": {"s": "on", "a": {}}})
        assert state is not None
        assert state["state"] == "on"
        assert client.get_state("light.test")["state"] == "on"

    def test_removed_only(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A diff without additions returns None.
        """
        client, _ = ha_client
        state = client._merge_diff("light.test", {"-": {"a": ["brightness"]}})
        assert state is None

    def test_context(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A string context and last_changed are merged.
        """
        client, _ = ha_client
        client.states["light.test"] = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {},
            "context": {"id": "old", "parent_id": None, "user_id": None},
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
        client._merge_diff("light.test", {"+": {"c": "new-ctx", "lc": 1000}})
        state = client.get_state("light.test")
        assert state["context"]["id"] == "new-ctx"
        assert state["last_changed"] == "1970-01-01T00:16:40+00:00"
        assert state["last_updated"] == "1970-01-01T00:16:40+00:00"

    def test_context_dict(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A dict context is merged.
        """
        client, _ = ha_client
        client.states["light.test"] = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {},
            "context": {"id": "old"},
        }
        client._merge_diff("light.test", {"+": {"c": {"user_id": "user-1"}}})
        state = client.get_state("light.test")
        assert state["context"] == {"id": "old", "user_id": "user-1"}

    def test_last_updated(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A last_updated timestamp is merged.
        """
        client, _ = ha_client
        client.states["light.test"] = {
            "entity_id": "light.test",
            "state": "on",
            "attributes": {},
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
        client._merge_diff("light.test", {"+": {"lu": 2000}})
        state = client.get_state("light.test")
        assert state["last_changed"] == "2026-01-01T00:00:00+00:00"
        assert state["last_updated"] == "1970-01-01T00:33:20+00:00"


class TestSubscribeEntity:
    """
    Tests for L{megingjord.ha.HAWebSocketClient._subscribe_entity}.
    """

    def test_no_client(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Subscribing without a client does nothing.
        """
        client, _ = ha_client
        client.client = None
        client._subscribe_entity("light.test")

    @pytest.mark.asyncio
    async def test_done_cancelled(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A cancelled subscription task is ignored.
        """
        client, _ = ha_client
        task = asyncio.create_task(asyncio.sleep(1))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        client._on_subscribe_done("light.test", task)

    @pytest.mark.asyncio
    async def test_done_error(
        self,
        ha_client: tuple[HAWebSocketClient, MagicMock],
        caplog: Any,
    ) -> None:
        """
        A failed subscription task logs a warning.
        """
        client, _ = ha_client

        async def boom() -> None:
            raise Exception("boom")

        task = asyncio.create_task(boom())
        with pytest.raises(Exception):
            await task
        with caplog.at_level(logging.WARNING, logger="megingjord.ha"):
            client._on_subscribe_done("light.test", task)
        assert "Failed to subscribe to light.test" in caplog.text


class TestCallbackErrors:
    """
    Tests for callback error handling.
    """

    @pytest.mark.asyncio
    async def test_callback_error_logged(
        self,
        ha_client: tuple[HAWebSocketClient, MagicMock],
        caplog: Any,
    ) -> None:
        """
        A failing state callback logs an error.
        """
        client, _ = ha_client

        async def boom(state: dict | None) -> None:
            raise Exception("boom")

        client.subscribe("light.test", boom)
        with caplog.at_level(logging.ERROR, logger="megingjord.ha"):
            client._notify(
                "light.test", {"entity_id": "light.test", "state": "on"}
            )
            await asyncio.sleep(0.05)
        assert "Error in state callback for light.test" in caplog.text

    @pytest.mark.asyncio
    async def test_callback_cancelled(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A cancelled callback task is ignored.
        """
        client, _ = ha_client

        async def slow(state: dict | None) -> None:
            await asyncio.sleep(1)

        client.subscribe("light.test", slow)
        client._notify(
            "light.test", {"entity_id": "light.test", "state": "on"}
        )
        await asyncio.sleep(0.01)
        for task in list(client.tasks):
            task.cancel()
        await asyncio.sleep(0.01)
        assert client.tasks == set()

    @pytest.mark.asyncio
    async def test_callback_error_no_entity(
        self,
        ha_client: tuple[HAWebSocketClient, MagicMock],
        caplog: Any,
    ) -> None:
        """
        A failing disconnect callback logs an error without an entity.
        """
        client, _ = ha_client

        async def boom(state: dict | None) -> None:
            raise Exception("boom")

        client.subscribe("light.test", boom)
        with caplog.at_level(logging.ERROR, logger="megingjord.ha"):
            await client._notify_all()
            await asyncio.sleep(0.05)
        assert "Error in state callback" in caplog.text


def make_alarm_tile(
    state: dict | None, arm_service: str = "alarm_arm_night"
) -> HAAlarmTile:
    """
    An alarm tile with mocked HA client, controller and deck.
    """
    ha = MagicMock()
    ha.subscribe.return_value = lambda: None
    ha.get_state.return_value = state
    ha.is_missing.return_value = False
    ha.get_entity_icon = AsyncMock(
        side_effect=lambda entity_id, attributes, icon=None, state=None: (
            get_entity_icon(entity_id, attributes, icon)
        )
    )
    ha.call_service = AsyncMock()
    tile = HAAlarmTile(
        0, ha, "alarm_control_panel.home_alarm", arm_service=arm_service
    )
    tile.controller = AsyncMock()
    tile.controller.draw_tile.return_value = b"tile"
    tile.controller.get_color = MagicMock(return_value="#996633")
    tile.controller.start_key_animation = MagicMock()
    tile.controller.stop_key_animation = MagicMock()
    tile.deck = MagicMock()
    return tile


def make_alarm_dial(state: dict | None) -> HAAlarmDial:
    """
    An alarm dial with mocked HA client, controller and deck.
    """
    ha = MagicMock()
    ha.subscribe.return_value = lambda: None
    ha.get_state.return_value = state
    ha.call_service = AsyncMock()
    dial = HAAlarmDial(0, ha, "alarm_control_panel.home_alarm")
    dial.controller = AsyncMock()
    dial.controller.draw_dial_tile_scroller.return_value = Image.new(
        "RGBA", (140, 100)
    )
    dial.deck = MagicMock()
    return dial


class TestHAAlarmTile:
    """
    Tests for L{megingjord.ha.HAAlarmTile}.
    """

    @pytest.mark.asyncio
    async def test_set_tile_disarmed(self) -> None:
        """
        A disarmed alarm shows the shield-off icon, inactive.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-inactive", "tile-bg": "tile-inactive-bg"},
            "shield-off",
            subtitle="Disarmed",
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_armed(self) -> None:
        """
        An armed alarm shows the state icon, active.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "armed_home",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-ok"},
            "shield-home",
            subtitle="Armed home",
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_disarming(self) -> None:
        """
        A disarming alarm shows the icon inactive.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarming",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-inactive", "tile-bg": "tile-inactive-bg"},
            "shield",
            subtitle="Disarming",
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_arming(self) -> None:
        """
        An arming alarm shows the warning icon and pulses.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "arming",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-warning"},
            "shield",
            subtitle="Arming",
            badge=None,
        )
        tile.controller.start_key_animation.assert_called_once_with(
            0, tile._render_pulse
        )

    @pytest.mark.asyncio
    async def test_set_tile_triggered(self) -> None:
        """
        A triggered alarm shows the bell icon, active.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "triggered",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-alert"},
            "bell-ring",
            subtitle="Triggered",
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_pulse_stops_on_armed(self) -> None:
        """
        Leaving a pulsing state stops the animation.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "pending",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.ha.get_state.return_value = {
            "entity_id": "alarm_control_panel.home_alarm",
            "state": "armed_home",
            "attributes": {"friendly_name": "Home Alarm"},
        }
        await tile.set_tile()
        tile.controller.stop_key_animation.assert_called_once_with(0)

    @pytest.mark.asyncio
    async def test_render_pulse(self) -> None:
        """
        The pulse renders with a modulated icon color.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "pending",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        image = await tile._render_pulse(math.pi)
        assert image == b"tile"
        colors = tile.controller.draw_tile.await_args.args[1]
        assert colors == {"icon-primary": "rgba(153, 102, 51, 1.00)"}

    @pytest.mark.asyncio
    async def test_render_pulse_reuses_frames(self) -> None:
        """
        The pulse reuses frames with the same alpha.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "pending",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        await tile._render_pulse(math.pi)
        await tile._render_pulse(math.pi)
        await tile._render_pulse(math.pi)
        assert tile.controller.draw_tile.await_count == 2

    @pytest.mark.asyncio
    async def test_render_pulse_frames_cleared(self) -> None:
        """
        Starting the pulse again clears the cached frames.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "pending",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        await tile._render_pulse(math.pi)
        count = tile.controller.draw_tile.await_count
        tile._start_pulse("icon-alert")
        await tile._render_pulse(math.pi)
        assert tile.controller.draw_tile.await_count == count + 1

    @pytest.mark.asyncio
    async def test_start_pulse_no_controller(self) -> None:
        """
        Starting the pulse without a controller does nothing.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "pending",
                "attributes": {},
            }
        )
        tile.controller = None
        tile._start_pulse("icon-warning")

    @pytest.mark.asyncio
    async def test_stop_pulse_no_controller(self) -> None:
        """
        Stopping the pulse without a controller does nothing.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "pending",
                "attributes": {},
            }
        )
        tile.controller = None
        tile._stop_pulse()

    def test_pulse_alpha(self) -> None:
        """
        The pulse alpha eases in and out between 0.25 and 1.0.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "pending",
                "attributes": {},
            }
        )
        assert tile._pulse_alpha(0) == 0.25
        assert tile._pulse_alpha(math.pi) == 1.0
        assert tile._pulse_alpha(2 * math.pi) == 0.25
        assert tile._pulse_alpha(math.pi / 2) == 0.625

    @pytest.mark.asyncio
    async def test_stop_cancels_pulse(self) -> None:
        """
        Stopping the tile stops the animation.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "triggered",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        await tile.stop()
        tile.controller.stop_key_animation.assert_called_once_with(0)

    @pytest.mark.asyncio
    async def test_key_arms(self) -> None:
        """
        Pressing a disarmed alarm arms with the configured service.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {},
            },
            arm_service="alarm_arm_night",
        )
        await tile.on_key_change(True)
        tile.ha.call_service.assert_awaited_once_with(
            "alarm_control_panel",
            "alarm_arm_night",
            entity_id="alarm_control_panel.home_alarm",
        )

    @pytest.mark.asyncio
    async def test_key_disarms(self) -> None:
        """
        Pressing an armed alarm disarms it.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "armed_away",
                "attributes": {},
            }
        )
        await tile.on_key_change(True)
        tile.ha.call_service.assert_awaited_once_with(
            "alarm_control_panel",
            "alarm_disarm",
            entity_id="alarm_control_panel.home_alarm",
        )

    @pytest.mark.asyncio
    async def test_key_arming_disarms(self) -> None:
        """
        Pressing an arming alarm disarms it, like the HA UI.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "arming",
                "attributes": {},
            }
        )
        await tile.on_key_change(True)
        tile.ha.call_service.assert_awaited_once_with(
            "alarm_control_panel",
            "alarm_disarm",
            entity_id="alarm_control_panel.home_alarm",
        )

    @pytest.mark.asyncio
    async def test_key_triggered_disarms(self) -> None:
        """
        Pressing a triggered alarm disarms it.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "triggered",
                "attributes": {},
            }
        )
        await tile.on_key_change(True)
        tile.ha.call_service.assert_awaited_once_with(
            "alarm_control_panel",
            "alarm_disarm",
            entity_id="alarm_control_panel.home_alarm",
        )

    @pytest.mark.asyncio
    async def test_key_disarming_disarms(self) -> None:
        """
        Pressing a disarming alarm disarms it.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarming",
                "attributes": {},
            }
        )
        await tile.on_key_change(True)
        tile.ha.call_service.assert_awaited_once_with(
            "alarm_control_panel",
            "alarm_disarm",
            entity_id="alarm_control_panel.home_alarm",
        )

    @pytest.mark.asyncio
    async def test_key_release(self) -> None:
        """
        Releasing the key does nothing.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {},
            }
        )
        await tile.on_key_change(False)
        tile.ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_key_no_state(self) -> None:
        """
        Pressing without a state does nothing.
        """
        tile = make_alarm_tile(None)
        await tile.on_key_change(True)
        tile.ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_key_service_error(self) -> None:
        """
        A failed service call redraws the tile.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {},
            }
        )
        tile.ha.call_service = AsyncMock(side_effect=Exception("boom"))
        await tile.on_key_change(True)
        tile.controller.draw_tile.assert_awaited()

    @pytest.mark.asyncio
    async def test_set_tile_no_controller(self) -> None:
        """
        Without a controller, nothing is drawn.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {},
            }
        )
        tile.controller = None
        await tile.set_tile()

    @pytest.mark.asyncio
    async def test_set_tile_no_state(self) -> None:
        """
        Without a state, the tile shows disconnected.
        """
        tile = make_alarm_tile(None)
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Disconnected",
            {"icon-primary": "icon-inactive", "tile-bg": "tile-inactive-bg"},
            "cloud-question-outline",
            subtitle=None,
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_unknown_state(self) -> None:
        """
        An unknown state shows the icon active.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "unknown",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-active"},
            "shield",
            subtitle="Unknown",
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_pending(self) -> None:
        """
        A pending alarm shows the alert icon and pulses.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "pending",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-warning"},
            "shield-outline",
            subtitle="Pending",
            badge=None,
        )
        tile.controller.start_key_animation.assert_called_once_with(
            0, tile._render_pulse
        )

    @pytest.mark.asyncio
    async def test_set_tile_custom_icon(self) -> None:
        """
        A custom icon is kept over the alarm state icon.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "armed_away",
                "attributes": {
                    "friendly_name": "Home Alarm",
                    "icon": "mdi:custom",
                },
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-ok"},
            "custom",
            subtitle="Armed away",
            badge=None,
        )

    @pytest.mark.asyncio
    async def test_set_tile_unavailable(self) -> None:
        """
        An unavailable alarm shows an alert badge.
        """
        tile = make_alarm_tile(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "unavailable",
                "attributes": {"friendly_name": "Home Alarm"},
            }
        )
        await tile.set_tile()
        tile.controller.draw_tile.assert_awaited_once_with(
            "Home Alarm",
            {"icon-primary": "icon-inactive", "tile-bg": "tile-inactive-bg"},
            "shield",
            subtitle="Unavailable",
            badge="alert-circle",
        )


class TestAlarmModeScrollerItem:
    """
    Tests for L{megingjord.ha.AlarmModeScrollerItem}.
    """

    def test_known_state(self) -> None:
        """
        Known states use the alarm titles and icons.
        """
        item = AlarmModeScrollerItem("armed_away", current=True)
        assert item.title == "Armed away"
        assert item.icon == "shield-lock"
        assert item.current

    def test_unknown_state(self) -> None:
        """
        Unknown states fall back to generic title and icon.
        """
        item = AlarmModeScrollerItem("unavailable")
        assert item.title == "Unavailable"
        assert item.icon == "shield"
        assert not item.current
        assert item.subtitle is None


class TestHAAlarmDial:
    """
    Tests for L{megingjord.ha.HAAlarmDial}.
    """

    @pytest.mark.asyncio
    async def test_update_view(self) -> None:
        """
        The view lists disarmed and all supported arm modes.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.update_view()
        assert dial.current_view is not None
        assert [item.wrapped for item in dial.current_view.items] == [
            "disarmed",
            "armed_home",
            "armed_away",
            "armed_night",
        ]
        assert dial.current_view.selected == 0

    @pytest.mark.asyncio
    async def test_update_view_no_state(self) -> None:
        """
        Without a state, only disarmed is offered.
        """
        dial = make_alarm_dial(None)
        await dial.update_view()
        assert [item.wrapped for item in dial.current_view.items] == [
            "disarmed"
        ]
        assert dial.current_view.selected == 0

    @pytest.mark.asyncio
    async def test_update_view_filters_features(self) -> None:
        """
        Unsupported arm modes are not offered.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 1},
            }
        )
        await dial.update_view()
        assert [item.wrapped for item in dial.current_view.items] == [
            "disarmed",
            "armed_home",
        ]

    @pytest.mark.asyncio
    async def test_update_view_all_features(self) -> None:
        """
        All arm modes are offered when all features are supported.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 63},
            }
        )
        await dial.update_view()
        assert [item.wrapped for item in dial.current_view.items] == [
            "disarmed",
            "armed_home",
            "armed_away",
            "armed_night",
            "armed_custom_bypass",
            "armed_vacation",
        ]

    @pytest.mark.asyncio
    async def test_update_view_transient(self) -> None:
        """
        A transient state is not an item; the selection is kept.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.update_view()
        dial.current_view.selected = 1
        dial.ha.get_state.return_value = {
            "entity_id": "alarm_control_panel.home_alarm",
            "state": "arming",
            "attributes": {"supported_features": 7},
        }
        await dial.update_view()
        assert [item.wrapped for item in dial.current_view.items] == [
            "disarmed",
            "armed_home",
            "armed_away",
            "armed_night",
        ]
        assert dial.current_view.selected == 1
        assert not any(item.current for item in dial.current_view.items)

    @pytest.mark.asyncio
    async def test_on_dial_turn(self) -> None:
        """
        Turning the dial cycles the selection.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.update_view()
        await dial.on_dial_turn(1)
        assert dial.current_view.selected == 1
        await dial.on_dial_turn(-1)
        assert dial.current_view.selected == 0
        await dial.on_dial_turn(-1)
        assert dial.current_view.selected == 0
        dial.controller.render_lcd.assert_awaited()

    @pytest.mark.asyncio
    async def test_on_dial_turn_no_controller(self) -> None:
        """
        Turning without a controller does nothing.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        dial.controller = None
        await dial.on_dial_turn(1)

    @pytest.mark.asyncio
    async def test_on_dial_push_release(self) -> None:
        """
        Releasing the dial does nothing.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.update_view()
        await dial.on_dial_push(False)
        dial.ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_dial_push_arms(self) -> None:
        """
        Pushing an arm mode arms the alarm.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.update_view()
        dial.current_view.selected = 1
        await dial.on_dial_push(True)
        dial.ha.call_service.assert_awaited_once_with(
            "alarm_control_panel",
            "alarm_arm_home",
            entity_id="alarm_control_panel.home_alarm",
        )

    @pytest.mark.asyncio
    async def test_on_dial_push_disarms(self) -> None:
        """
        Pushing disarmed disarms the alarm.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "armed_away",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.update_view()
        dial.current_view.selected = 0
        await dial.on_dial_push(True)
        dial.ha.call_service.assert_awaited_once_with(
            "alarm_control_panel",
            "alarm_disarm",
            entity_id="alarm_control_panel.home_alarm",
        )

    @pytest.mark.asyncio
    async def test_on_dial_push_service_error(self) -> None:
        """
        A failed service call is logged, not raised.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        dial.ha.call_service = AsyncMock(side_effect=Exception("boom"))
        await dial.update_view()
        dial.current_view.selected = 1
        await dial.on_dial_push(True)

    @pytest.mark.asyncio
    async def test_on_dial_push_code_required(self) -> None:
        """
        Arming an alarm that requires a code does nothing.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {
                    "supported_features": 7,
                    "code_arm_required": True,
                },
            }
        )
        await dial.update_view()
        dial.current_view.selected = 1
        await dial.on_dial_push(True)
        dial.ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_state_no_controller(self) -> None:
        """
        A state change without a controller only updates the view.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        dial.controller = None
        dial.ha.get_state.return_value = {
            "entity_id": "alarm_control_panel.home_alarm",
            "state": "armed_away",
            "attributes": {"supported_features": 7},
        }
        await dial.on_state(dial.ha.get_state.return_value)
        assert dial.current_view.selected == 2

    @pytest.mark.asyncio
    async def test_on_state(self) -> None:
        """
        A state change rebuilds the view.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.update_view()
        dial.ha.get_state.return_value = {
            "entity_id": "alarm_control_panel.home_alarm",
            "state": "armed_away",
            "attributes": {"supported_features": 7},
        }
        await dial.on_state(dial.ha.get_state.return_value)
        assert dial.current_view.selected == 2
        assert dial.current_view.selected_item.current
        dial.controller.render_lcd.assert_awaited_once_with(tile_changed=0)

    @pytest.mark.asyncio
    async def test_render(self) -> None:
        """
        Rendering draws the scroller view.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.update_view()
        await dial.render()
        dial.controller.draw_dial_tile_scroller.assert_awaited_once_with(
            dial.current_view, mini=False
        )

    @pytest.mark.asyncio
    async def test_render_no_controller(self) -> None:
        """
        Rendering without a controller returns a blank image.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        dial.controller = None
        image = await dial.render()
        assert image.size == (140, 100)

    @pytest.mark.asyncio
    async def test_start(self) -> None:
        """
        Starting subscribes and renders the initial view.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        await dial.start(MagicMock())
        dial.ha.subscribe.assert_called_once_with(
            "alarm_control_panel.home_alarm", dial.on_state
        )
        dial.controller.draw_dial_tile_scroller.assert_awaited_once_with(
            dial.current_view, mini=False
        )

    @pytest.mark.asyncio
    async def test_stop(self) -> None:
        """
        Stopping unsubscribes and redraws the tile.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        dial.ha.subscribe.return_value = MagicMock()
        await dial.start(MagicMock())
        await dial.stop()
        dial.ha.subscribe.return_value.assert_called_once_with()
        dial.controller.render_lcd.assert_awaited_with(tile_changed=0)

    @pytest.mark.asyncio
    async def test_stop_no_controller(self) -> None:
        """
        Stopping without a controller only unsubscribes.
        """
        dial = make_alarm_dial(
            {
                "entity_id": "alarm_control_panel.home_alarm",
                "state": "disarmed",
                "attributes": {"supported_features": 7},
            }
        )
        dial.ha.subscribe.return_value = MagicMock()
        await dial.start(MagicMock())
        dial.controller = None
        await dial.stop()
        dial.ha.subscribe.return_value.assert_called_once_with()
