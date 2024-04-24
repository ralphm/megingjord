"""
SVG Icon utilities.
"""

import logging
from pathlib import Path
import re

import aiohttp
from svgelements import SVG
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


async def get_icon(icon: str, size: int) -> SVG:
    """
    Get an icon.

    Icons are retrieved from the MaterialDesign (mdi) repository and cached as
    local files. The resulting SVG object is not cached.
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
