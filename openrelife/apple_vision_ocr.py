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


import numpy as np


def _np_image_to_cgimage(image: np.ndarray):
    """Convert an HxWx3 RGB uint8 numpy array to a (CGImage, ns_data) tuple.

    Returns BOTH the CGImage and the NSData that backs its pixel buffer.
    The caller MUST keep the NSData reference alive until after the CGImage
    has been consumed (e.g. after performRequests_error_ returns), otherwise
    the underlying buffer is freed and Vision reads garbage memory.
    """
    import Foundation
    import Quartz
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(
            f"Expected HxWx3 uint8 RGB image, got shape={image.shape} dtype={image.dtype}"
        )
    h, w, _ = image.shape
    if not image.flags["C_CONTIGUOUS"]:
        image = np.ascontiguousarray(image)
    raw_bytes = image.tobytes()
    # NSData copies the bytes — survives even if `raw_bytes` is GC'd.
    ns_data = Foundation.NSData.dataWithBytes_length_(raw_bytes, len(raw_bytes))
    provider = Quartz.CGDataProviderCreateWithCFData(ns_data)
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    bits_per_component = 8
    bits_per_pixel = 24
    bytes_per_row = w * 3
    bitmap_info = Quartz.kCGImageAlphaNone | Quartz.kCGBitmapByteOrderDefault
    cg = Quartz.CGImageCreate(
        w, h,
        bits_per_component, bits_per_pixel, bytes_per_row,
        color_space, bitmap_info,
        provider, None, False, Quartz.kCGRenderingIntentDefault,
    )
    if cg is None:
        raise RuntimeError("CGImageCreate returned NULL")
    return cg, ns_data


def _utf16_offset(text: str, char_index: int) -> int:
    """Convert a Python str index (UTF-32 code points) to the equivalent
    UTF-16 code-unit offset, which is what NSRange / NSString use.
    """
    return len(text[:char_index].encode("utf-16-le")) // 2


def _words_from_observation(observation) -> list:
    """Extract per-word coords from a VNRecognizedTextObservation.
    Returns a list of {text, x1, y1, x2, y2} entries (top-left origin).
    """
    candidates = observation.topCandidates_(1)
    if not candidates or len(candidates) == 0:
        return []
    top = candidates[0]
    full_string = str(top.string())
    if not full_string:
        return []
    out = []
    cursor = 0
    for token in full_string.split():
        idx = full_string.find(token, cursor)
        if idx < 0:
            continue
        cursor = idx + len(token)
        utf16_start = _utf16_offset(full_string, idx)
        utf16_end = _utf16_offset(full_string, idx + len(token))
        ns_range = (utf16_start, utf16_end - utf16_start)
        try:
            bbox_obs, error = top.boundingBoxForRange_error_(ns_range, None)
        except Exception:
            continue
        if error is not None or bbox_obs is None:
            continue
        tl = bbox_obs.topLeft()
        br = bbox_obs.bottomRight()
        bbox = _normalize_bbox((tl.x, tl.y), (br.x, br.y))
        out.append({"text": token, **bbox})
    return out


def extract_text_with_vision(image: np.ndarray):
    """Run Apple Vision OCR on an RGB image.

    Returns (text, words_with_coords) matching the doctr backend's schema.
    Raises RuntimeError on Vision failure (caller catches and falls back to doctr).
    """
    import Vision
    cg_image, ns_data = _np_image_to_cgimage(image)
    # `ns_data` MUST remain in scope until performRequests_error_ returns.
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    request.setRecognitionLanguages_(_system_recognition_languages())

    success, error = handler.performRequests_error_([request], None)
    # keep ns_data alive past performRequests; do not remove
    _ = ns_data
    if not success:
        msg = error.localizedDescription() if error is not None else "unknown error"
        raise RuntimeError(f"Vision performRequests failed: {msg}")

    observations = request.results() or []
    text_parts: list = []
    words_with_coords: list = []
    for obs in observations:
        candidates = obs.topCandidates_(1)
        if not candidates or len(candidates) == 0:
            continue
        line_text = str(candidates[0].string())
        if line_text:
            text_parts.append(line_text)
        words_with_coords.extend(_words_from_observation(obs))

    text = "\n".join(text_parts)
    return text, words_with_coords
