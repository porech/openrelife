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
