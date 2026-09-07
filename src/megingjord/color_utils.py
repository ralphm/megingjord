# SPDX-License-Identifier: MIT

"""
Color conversion utils.
"""

import colorsys


def hex_to_rgb(hex_str: str) -> tuple[int, ...]:
    """
    Convert a hex string to an RGB tuple.

    >>> hex_to_rgb('#CC0000')
    (204, 0, 0)
    """
    if hex_str.startswith("#"):
        hex_str = hex_str[1:]

    return tuple(
        (int(hex_str[i : i + 2], 16) for i in range(0, len(hex_str), 2))
    )


def rgb_to_hex(rgb: tuple[int, ...]) -> str:
    """
    Convert an RGB tuple to hex string.

    >>> rgb_to_hex((204, 0, 0))
    '#CC0000'
    """
    return "#" + "".join([f"{c:02X}" for c in rgb])


def scale_rgb_down(rgb: tuple[int, ...]) -> tuple[float, ...]:
    """Scales an RGB tuple down to values between 0 and 1.

    >>> scale_rgb_tuple((204, 0, 0))
    (.80, 0, 0)
    """
    return tuple((round(float(c) / 255, 2) for c in rgb))


def scale_rgb_up(rgb: tuple[float, ...]) -> tuple[int, ...]:
    """Scales an RGB tuple up or down to/from values between 0 and 1.

    >>> scale_rgb_tuple((.80, 0, 0), False)
    (204, 0, 0)
    """
    return tuple((max(0, min(255, int(c * 255))) for c in rgb))


def is_dark(r: float, g: float, b: float) -> bool:
    """
    Is this color dark?
    """
    luminance = r * 0.299 + g * 0.587 + b * 0.114
    return luminance < 0.52


def make_triad(hex_str: str) -> list[str]:
    """
    Create a triad of colors from a single (background) color.

    The second and third color are progressively lighter.

    >> make_triad('#990000')
    ['#990000', '#B72424', '#FFA5A5']
    >> make_triad('#336699')
    ['#336699', '#5586B7', '#C3E1FF']
    """
    colors = [hex_str]

    if hex_str.startswith("#"):
        hex_str = hex_str[1:]

    orig_rgb = hex_to_rgb(hex_str)
    rgb = scale_rgb_down(orig_rgb)
    hue, sat, val = colorsys.rgb_to_hsv(*rgb)

    if is_dark(*rgb):
        second = (hue, min(1, sat * 0.6), max(0.05, min(1, val * 1.4)))
    else:
        second = (hue, 0.1, 0.4)
    colors.append(rgb_to_hex(scale_rgb_up(colorsys.hsv_to_rgb(*second))))

    if is_dark(*rgb):
        third = (hue, min(1, sat * 0.35), max(0.1, min(1, val * 1.8)))
    else:
        third = (hue, 0.1, 0.1)
    colors.append(rgb_to_hex(scale_rgb_up(colorsys.hsv_to_rgb(*third))))

    return colors


def black_or_white(hex_str: str) -> str:
    """
    Return contrasting black or white, depending on the input color.
    """
    orig_rgb = hex_to_rgb(hex_str)
    rgb = scale_rgb_down(orig_rgb)
    if is_dark(*rgb):
        return "#FFFFFF"
    return "#000000"


def xyb_to_rgb(
    x: float, y: float, brightness: float
) -> tuple[float, float, float]:
    """
    Convert a CIE XY gamut position to RGB, given a certain brightness.

    Parameters C{x} and C{y} are in the range [-1, 1], C{brightness} in [0, 1].
    The returned values should be floats in the range [0, 1], and should be
    capped beyond that.
    """
    # pylint: disable=invalid-name
    z: float = 1.0 - x - y
    Y: float = brightness
    X: float = (Y / y) * x
    Z: float = (Y / y) * z

    # Convert to RGB using Wide RGB D65 conversion
    r: float = X * 1.656492 - Y * 0.354851 - Z * 0.255038
    g: float = -X * 0.707196 + Y * 1.655397 + Z * 0.036152
    b: float = X * 0.051713 - Y * 0.121364 + Z * 1.011530

    return (r, g, b)


COLOR_THEMES = {
    "default": {
        "black": "#000000",
        "white": "#ffffff",
        "red": "#990000",
        "dark-red": "#330000",
        "gray": "#333333",
        "dark-blue": "#336699",
        "blue": "#6699cc",
        "light-blue": "#c0e0ff",
        "orange": "#996633",
        "green": "#009900",
        "tile-fg": "light-blue",
        "tile-bg": "dark-blue",
        "tile-inactive-bg": "black",
        "icon-primary": "light-blue",
        "icon-secondary": "blue",
        "icon-alert": "red",
        "icon-ok": "green",
        "icon-warning": "orange",
        "icon-inactive": "blue",
        "icon-active": "light-blue",
        "lcd-bg": "black",
        "status-bar-fg": "white",
        "status-bar-bg": "dark-blue",
        "status-bar-border": "blue",
        "dial-title": "white",
        "dial-icon": "white",
        "dial-bar-label": "white",
        "dial-bar-bg": "dark-blue",
        "dial-bar-fill": "blue",
        "google-meet-fg": "white",
        "google-meet-secondary-bg": "gray",
        "google-meet-secondary-icon": "white",
        "google-meet-active-bg": "dark-blue",
        "google-meet-active-icon": "white",
        "google-meet-inactive-bg": "gray",
        "google-meet-inactive-icon": "white",
        "google-meet-unmuted-bg": "gray",
        "google-meet-unmuted-icon": "white",
        "google-meet-muted-bg": "orange",
        "google-meet-muted-icon": "white",
        "google-meet-hangup-bg": "red",
        "google-meet-hangup-icon": "google-meet-muted-icon",
    },
    "dracula": {
        "dracula-bg": "#282a36",
        "dracula-current": "#44475a",
        "dracula-selection": "dracula-current",
        "dracula-fg": "#f8f8f2",
        "dracula-comment": "#6272a4",
        "dracula-cyan": "#8be9fd",
        "dracula-green": "#50fa7b",
        "dracula-orange": "#ffb86c",
        "dracula-pink": "#ff79c6",
        "dracula-purple": "#bd93f9",
        "dracula-red": "#ff5555",
        "dracula-yellow": "#f1fa8c",
        "dracula-bright-cyan": "#a4ffff",
        "dracula-bright-green": "#69ff94",
        # dracula-bright-orange
        "dracula-bright-pink": "#ff92df",
        "dracula-bright-purple": "#d6acff",
        "dracula-bright-red": "#ff6e6e",
        "dracula-bright-yellow": "#ffffa5",
        "dracula-bright-white": "#ffffff",
        "dracula-bglighter": "#424450",
        "dracula-bglight": "#343746",
        "dracula-bgdark": "#21222c",
        "dracula-bgdarker": "#191a21",
        "ansi-black": "dracula-bgdark",
        "ansi-red": "dracula-red",
        "ansi-green": "dracula-green",
        "ansi-yellow": "dracula-yellow",
        "ansi-blue": "dracula-purple",
        "ansi-magenta": "dracula-pink",
        "ansi-cyan": "dracula-cyan",
        "ansi-white": "dracula-fg",
        "ansi-bright-black": "dracula-comment",
        "ansi-bright-red": "dracula-bright-red",
        "ansi-bright-green": "dracula-bright-green",
        "ansi-bright-yellow": "dracula-bright-yellow",
        "ansi-bright-blue": "dracula-bright-purple",
        "ansi-bright-magenta": "dracula-bright-pink",
        "ansi-bright-cyan": "dracula-bright-cyan",
        "ansi-bright-white": "dracula-bright-white",
        "tile-fg": "dracula-fg",
        "tile-bg": "dracula-bg",
        "tile-inactive-bg": "dracula-bgdarker",
        "icon-primary": "dracula-purple",
        "icon-secondary": "dracula-current",
        "icon-warning": "dracula-orange",
        "icon-alert": "dracula-red",
        "icon-ok": "dracula-green",
        "icon-inactive": "dracula-current",
        "icon-active": "dracula-purple",
        "lcd-bg": "dracula-bgdarker",
        "status-bar-fg": "dracula-fg",
        "status-bar-bg": "dracula-bg",
        "status-bar-border": "dracula-bgdark",
        "dial-title": "dracula-fg",
        "dial-icon": "dracula-fg",
        "dial-bar-label": "dracula-fg",
        "dial-bar-bg": "dracula-bg",
        "dial-bar-fill": "dracula-purple",
        "google-meet-fg": "tile-fg",
        "google-meet-secondary-bg": "tile-bg",
        "google-meet-secondary-icon": "icon-secondary",
        "google-meet-active-bg": "tile-bg",
        "google-meet-active-icon": "icon-active",
        "google-meet-inactive-bg": "tile-bg",
        "google-meet-inactive-icon": "icon-inactive",
        "google-meet-unmuted-bg": "tile-bg",
        "google-meet-unmuted-icon": "icon-active",
        "google-meet-muted-bg": "tile-bg",
        "google-meet-muted-icon": "icon-warning",
        "google-meet-hangup-bg": "tile-bg",
        "google-meet-hangup-icon": "icon-alert",
    },
}


def get_color(theme: dict[str, str], name: str) -> str:
    """
    Recursively resolve color names until we get a numeric result.
    """
    color = theme[name]
    if color.startswith("#"):
        return color
    return get_color(theme, color)


def get_colors(theme_name: str) -> dict[str, str]:
    """
    Get theme colors.
    """
    theme = COLOR_THEMES[theme_name]

    palette: dict[str, str] = {}
    for name in theme.keys():
        palette[name] = get_color(theme, name)

    return palette
