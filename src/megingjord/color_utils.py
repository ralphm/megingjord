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


def rgb_to_hex(rgb):
    """
    Convert an RGB tuple to hex string.

    >>> rgb_to_hex((204, 0, 0))
    '#CC0000'
    """
    return "#" + "".join([f"{c:02X}" for c in rgb])


def scale_rgb_tuple(rgb, down=True):
    """Scales an RGB tuple up or down to/from values between 0 and 1.

    >>> scale_rgb_tuple((204, 0, 0))
    (.80, 0, 0)
    >>> scale_rgb_tuple((.80, 0, 0), False)
    (204, 0, 0)
    """
    if not down:
        return tuple((max(0, min(255, int(c * 255))) for c in rgb))
    return tuple((round(float(c) / 255, 2) for c in rgb))


def is_dark(r: float, g: float, b: float) -> bool:
    """
    Is this color dark?
    """
    luminance = r * 0.299 + g * 0.587 + b * 0.114
    return luminance < 0.52


def make_triad(hex_str):
    """
    Create a triad of colors from a single (background) color.

    The second and third color are progressively lighter.

    >> make_triad('#990000')
    ['#990000', '#B72424', '#FFA5A5']
    >> make_triad('#336699')
    ['#336699', '#5586B7', '#C3E1FF']
    """
    if hex_str.startswith("#"):
        hex_str = hex_str[1:]

    colors = [hex_str]
    orig_rgb = hex_to_rgb(hex_str)
    rgb = scale_rgb_tuple(orig_rgb)
    hue, sat, val = colorsys.rgb_to_hsv(*rgb)

    if is_dark(*rgb):
        second = (hue, min(1, sat * 0.6), max(0.05, min(1, val * 1.4)))
    else:
        second = (hue, 0.1, 0.4)
    colors.append(
        rgb_to_hex(scale_rgb_tuple(colorsys.hsv_to_rgb(*second), False))
    )

    if is_dark(*rgb):
        third = (hue, min(1, sat * 0.35), max(0.1, min(1, val * 1.8)))
    else:
        third = (hue, 0.1, 0.1)
    colors.append(
        rgb_to_hex(scale_rgb_tuple(colorsys.hsv_to_rgb(*third), False))
    )

    return colors


def black_or_white(hex_str: str) -> str:
    """
    Return contrasting black or white, depending on the input color.
    """
    orig_rgb = hex_to_rgb(hex_str)
    rgb = scale_rgb_tuple(orig_rgb)
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
    # pylint: disable=C0103
    z: float = 1.0 - x - y
    Y: float = brightness
    X: float = (Y / y) * x
    Z: float = (Y / y) * z

    # Convert to RGB using Wide RGB D65 conversion
    r: float = X * 1.656492 - Y * 0.354851 - Z * 0.255038
    g: float = -X * 0.707196 + Y * 1.655397 + Z * 0.036152
    b: float = X * 0.051713 - Y * 0.121364 + Z * 1.011530

    return (r, g, b)
