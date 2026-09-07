"""
Tests for L{megingjord.color_utils}.
"""

from megingjord import color_utils


class TestGetColor:
    """
    Tests for L{megingjord.color_utils.get_color}.
    """

    theme: dict[str, str] = {}

    @classmethod
    def setup_class(cls) -> None:
        """
        Setup theme.
        """

        cls.theme = {
            "blueish": "#006699",
            "icon-primary": "blueish",
        }

    def test_get_color(self) -> None:
        """
        Getting a color with a numeric value succeeds.
        """

        assert color_utils.get_color(self.theme, "blueish") == "#006699"

    def test_get_color_indirect(self) -> None:
        """
        Getting the final color through indirection succeeds.
        """

        assert color_utils.get_color(self.theme, "icon-primary") == "#006699"


class TestGetColors:
    """
    Tests for L{megingjord.color_utils.get_colors}.
    """

    theme: dict[str, str] = {}

    @classmethod
    def setup_class(cls) -> None:
        """
        Setup theme.
        """

        color_utils.COLOR_THEMES["test"] = {
            "blueish": "#006699",
            "icon-primary": "blueish",
        }

    def test_get_colors(self) -> None:
        """
        Retrieving the colors of a theme only returns numeric values.
        """

        colors = color_utils.get_colors("test")

        assert colors["blueish"] == "#006699"
        assert colors["icon-primary"] == "#006699"

    def test_icon_ok(self) -> None:
        """
        The icon-ok color is green in both themes.
        """

        assert color_utils.get_colors("default")["icon-ok"] == "#009900"
        assert color_utils.get_colors("dracula")["icon-ok"] == "#50fa7b"
