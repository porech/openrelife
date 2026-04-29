"""Apple Vision OCR backend (PyObjC bridge).

This module is safe to import on any platform: heavyweight framework imports
(Vision, Quartz) happen lazily inside is_apple_vision_available() and the
public extract function. On non-arm64-darwin systems, is_apple_vision_available()
short-circuits before attempting any Vision import.
"""
from __future__ import annotations

import functools
import platform
import sys


@functools.lru_cache(maxsize=1)
def is_apple_vision_available() -> bool:
    """True only on Apple Silicon Mac with macOS 13+ AND Vision frameworks
    importable. Cached for the lifetime of the process.
    """
    if sys.platform != "darwin":
        return False
    if platform.machine() != "arm64":
        return False
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        return False
    if major < 13:
        return False
    try:
        import Vision  # noqa: F401
        import Quartz  # noqa: F401
    except ImportError:
        return False
    return True


from typing import Dict, Tuple


def _normalize_bbox(top_left: Tuple[float, float],
                    bottom_right: Tuple[float, float]) -> Dict[str, float]:
    """Convert Vision (bottom-left origin) bbox to top-left origin bbox.

    Args:
        top_left: (x, y) where y is in [0, 1] measured from the bottom of the image.
        bottom_right: (x, y) likewise.

    Returns:
        {x1, y1, x2, y2} in top-left origin.
    """
    tl_x, tl_y = top_left
    br_x, br_y = bottom_right
    return {
        "x1": tl_x,
        "y1": round(1.0 - tl_y, 10),
        "x2": br_x,
        "y2": round(1.0 - br_y, 10),
    }
