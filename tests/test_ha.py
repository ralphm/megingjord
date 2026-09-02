"""
Tests for L{megingjord.ha}.
"""

from megingjord.ha import normalize_url


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
