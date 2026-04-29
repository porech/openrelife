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


from typing import Dict, List, Tuple


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


def _preferred_system_language() -> str:
    """First preferred system language as BCP-47 string (e.g. 'it-IT').

    Returns 'en-US' as a safe default if the lookup fails for any reason.
    """
    try:
        import Foundation  # PyObjC umbrella
        langs = Foundation.NSLocale.preferredLanguages()
        if langs and len(langs) > 0:
            return str(langs[0])
    except Exception:
        pass
    return "en-US"


def _vision_supported_languages() -> List[str]:
    """List of BCP-47 language codes supported by Vision text recognition
    at the Accurate level. Empty list on error (caller should fall back).
    """
    try:
        import Vision
        level = Vision.VNRequestTextRecognitionLevelAccurate
        revision = Vision.VNRecognizeTextRequest.currentRevision()
        result, error = Vision.VNRecognizeTextRequest \
            .supportedRecognitionLanguagesForTextRecognitionLevel_revision_error_(
                level, revision, None
            )
        if error is not None or result is None:
            return []
        return [str(lang) for lang in result]
    except Exception:
        return []


def _system_recognition_languages() -> List[str]:
    """Compose the recognitionLanguages list: [system_locale, 'en-US']
    deduplicated, with unsupported entries dropped. If the system locale is
    not supported by Vision, returns ['en-US'] only.
    """
    locale = _preferred_system_language()
    supported = set(_vision_supported_languages())
    out: List[str] = []
    for code in (locale, "en-US"):
        if code in supported and code not in out:
            out.append(code)
    if not out:
        out = ["en-US"]
    return out
