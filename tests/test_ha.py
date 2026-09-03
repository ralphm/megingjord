"""
Tests for L{megingjord.ha}.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from PIL import Image

from megingjord.ha import (
    HAEntityDial,
    HAEntityTile,
    HAWebSocketClient,
    get_entity_icon,
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
        Connecting fetches states, subscribes and notifies subscribers.
        """
        client, mock_class = ha_client
        mock = mock_class.return_value
        mock.connect = AsyncMock()
        state = {"entity_id": "light.test", "state": "off", "attributes": {}}
        mock.get_states = AsyncMock(return_value=[state])
        mock.subscribe_events = AsyncMock()
        mock.start_listening = AsyncMock()

        done = asyncio.Event()
        states: list[dict | None] = []

        async def on_state(state: dict | None) -> None:
            states.append(state)
            if len(states) == 2:
                done.set()

        client.subscribe("light.test", on_state)

        await client._connect()

        mock.connect.assert_awaited_once()
        mock.get_states.assert_awaited_once()
        mock.subscribe_events.assert_awaited_once()
        assert mock.subscribe_events.call_args.args[1] == "state_changed"
        assert client.connected is False
        assert client.get_state("light.test") is None
        await asyncio.wait_for(done.wait(), 1)
        assert states == [state, None]

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
        mock.get_states = AsyncMock(
            return_value=[
                {"entity_id": "light.test", "state": "off", "attributes": {}}
            ]
        )
        mock.subscribe_events = AsyncMock()

        listening = asyncio.Event()
        release = asyncio.Event()

        async def start_listening() -> None:
            listening.set()
            await release.wait()

        mock.start_listening = start_listening

        task = asyncio.create_task(client._connect())
        await asyncio.wait_for(listening.wait(), 1)
        assert client.connected is True
        assert client.get_state("light.test") == {
            "entity_id": "light.test",
            "state": "off",
            "attributes": {},
        }
        release.set()
        await task
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_on_event(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A state_changed event updates the state and notifies subscribers.
        """
        client, _ = ha_client
        notified = asyncio.Event()
        states: list[dict | None] = []

        async def on_state(state: dict | None) -> None:
            states.append(state)
            notified.set()

        client.subscribe("light.test", on_state)
        client._on_event(
            {
                "event_type": "state_changed",
                "data": {
                    "entity_id": "light.test",
                    "new_state": {"entity_id": "light.test", "state": "on"},
                },
            }
        )
        await asyncio.wait_for(notified.wait(), 1)
        assert states == [{"entity_id": "light.test", "state": "on"}]
        assert client.get_state("light.test") == {
            "entity_id": "light.test",
            "state": "on",
        }

    @pytest.mark.asyncio
    async def test_on_event_removed(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        A removed entity state clears the state and notifies subscribers.
        """
        client, _ = ha_client
        client.states["light.test"] = {
            "entity_id": "light.test",
            "state": "on",
        }
        notified = asyncio.Event()
        states: list[dict | None] = []

        async def on_state(state: dict | None) -> None:
            states.append(state)
            notified.set()

        client.subscribe("light.test", on_state)
        client._on_event(
            {
                "event_type": "state_changed",
                "data": {"entity_id": "light.test", "new_state": None},
            }
        )
        await asyncio.wait_for(notified.wait(), 1)
        assert states == [None]
        assert client.get_state("light.test") is None

    def test_on_event_other_entity(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Events for other entities are ignored.
        """
        client, _ = ha_client
        called: list[dict | None] = []
        client.subscribe("light.other", lambda state: called.append(state))
        client._on_event(
            {
                "event_type": "state_changed",
                "data": {
                    "entity_id": "light.test",
                    "new_state": {"entity_id": "light.test", "state": "on"},
                },
            }
        )
        assert called == []
        assert client.get_state("light.test") == {
            "entity_id": "light.test",
            "state": "on",
        }

    def test_on_event_other_type(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        Non-state_changed events are ignored.
        """
        client, _ = ha_client
        client._on_event({"event_type": "call_service", "data": {}})
        assert client.states == {}

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
        mock.get_states = AsyncMock(return_value=[])
        mock.subscribe_events = AsyncMock()
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
        mock.get_states = AsyncMock(return_value=[])
        mock.subscribe_events = AsyncMock()
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
            "lightbulb",
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
            "Test Light", {"icon-primary": "icon-active"}, "lightbulb"
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
            {"icon-primary": "icon-alert", "tile-bg": "tile-inactive-bg"},
            "lightbulb",
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
        A state change renders the LCD.
        """
        await dial.on_state(None)
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
    async def test_render_number(self, dial: HAEntityDial) -> None:
        """
        A number entity renders its value as a fraction.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"friendly_name": "Volume", "min": 0, "max": 100},
        }
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
        Turning the dial sets the value and renders the LCD.
        """
        dial.ha.get_state.return_value = {
            "entity_id": "number.test",
            "state": "50",
            "attributes": {"min": 0, "max": 100},
        }
        dial.ha.call_service = AsyncMock()
        await dial.on_dial_turn(1)
        dial.ha.call_service.assert_awaited_once()
        value = dial.ha.call_service.await_args.kwargs["data"]["value"]
        assert value == pytest.approx(51.0)
        dial.controller.render_lcd.assert_awaited_once_with(tile_changed=0)

    @pytest.mark.asyncio
    async def test_on_dial_turn_disconnected(self, dial: HAEntityDial) -> None:
        """
        Turning the dial without a state does nothing.
        """
        dial.ha.call_service = AsyncMock()
        await dial.on_dial_turn(1)
        dial.ha.call_service.assert_not_called()
        dial.controller.render_lcd.assert_not_called()

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

    def test_on_event_no_entity(
        self, ha_client: tuple[HAWebSocketClient, MagicMock]
    ) -> None:
        """
        An event without an entity id is ignored.
        """
        client, _ = ha_client
        client._on_event({"event_type": "state_changed", "data": {}})
        assert client.states == {}

    @pytest.mark.asyncio
    async def test_stop_no_controller(self, dial: HAEntityDial) -> None:
        """
        Stopping without a controller does not render the LCD.
        """
        dial.controller = None
        await dial.stop()

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
