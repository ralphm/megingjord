# SPDX-License-Identifier: MIT

"""
SVG Icon utilities.
"""

import copy
import logging
import re
from io import BytesIO
from pathlib import Path

import aiohttp
from async_lru import alru_cache
from cairosvg import svg2png
from PIL import Image
from svgelements import SVG, Color, Matrix
from xdg_base_dirs import xdg_cache_home

ICON_CHARS = re.compile("^[a-z0-9-]+$")
MDI_BASE = (
    "https://raw.githubusercontent.com/Templarian/MaterialDesign/master/svg"
)


logger = logging.getLogger(__name__)


async def download_icon(icon: str, filename: Path) -> None:
    """
    Download icon file.
    """

    url = f"{MDI_BASE}/{icon}.svg"

    logger.info(f"Fetching icon from {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.read()
            with open(filename, "wb") as f:
                f.write(data)


@alru_cache(maxsize=128)
async def get_icon(icon: str, size: int) -> SVG:
    """
    Get an icon.

    Icons are retrieved from the MaterialDesign (mdi) repository and cached as
    local files. The parsed SVG is cached per icon and size.
    """

    if not ICON_CHARS.match(icon):
        raise ValueError("Invalid icon name")

    cache_dir = xdg_cache_home() / "megingjord/icons"
    cache_dir.mkdir(mode=0o775, parents=True, exist_ok=True)
    icon_filename = cache_dir / f"{icon}.svg"

    if not icon_filename.exists():
        logger.debug(f"Need to download {icon}")
        await download_icon(icon, icon_filename)
    else:
        logger.debug(f"Using cached icon {icon}")

    return SVG.parse(icon_filename, reify=False, width=size, height=size)


@alru_cache(maxsize=128)
async def svg_icon(
    icon: str, color: str, size: int, pos_x: int = 0, pos_y: int = 0
) -> SVG:
    """
    Draw an icon.

    This reads the icon from disk, applies the given color, and applies a
    matrix to scale and position with the given size and coordinates.
    """
    svg = copy.deepcopy(await get_icon(icon=icon, size=size))
    next(iter(svg)).fill = Color(color)

    if pos_x or pos_y:
        svg = svg * Matrix(f"translate({pos_x}, {pos_y})")

    return svg


def svg_to_image(svg: SVG, width: int, height: int) -> Image.Image:
    """
    Convert an SVG to a PIL Image.
    """
    png = BytesIO(
        svg2png(
            bytestring=svg.string_xml().encode("utf-8"),
            output_width=width,
            output_height=height,
        )
    )

    return Image.open(png)
